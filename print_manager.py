"""Cetak foto langsung (tanpa dialog) ke printer Windows.

Pengaturan ada di print_config.json (dibuat otomatis saat pertama dipakai).
Cek nama printer yang tersedia:   python print_manager.py --list
Tes cetak satu foto:              python print_manager.py --test path\\ke\\foto_frame.jpg

Ukuran kertas, jenis kertas, dan kualitas diambil dari *Printing Defaults* printer di Windows
(lihat README_PRINT.md), jadi atur sekali di sana.
"""
import json
import logging
import os
import subprocess
import sys

from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "print_config.json")

DEFAULT_CONFIG = {
    # Kosong = printer default Windows. Isi nama persis dari `python print_manager.py --list`
    "printer_name": "",
    # "fit"  = seluruh foto tampil, mungkin ada tepi putih
    # "fill" = foto memenuhi kertas, tepinya terpotong sedikit (cocok untuk kertas borderless)
    "mode": "fit",
    # Putar foto otomatis jika orientasi foto berbeda dengan kertas
    "auto_rotate": True,
    # Strip sempit (rasio lebar/tinggi <= 0.4, mis. 2x6) dicetak 2 lajur berdampingan
    # pada satu kertas 4x6 lalu dipotong dua. Matikan jika kertasmu memang ukuran strip.
    "duplicate_strip": True,
    "copies": 1,
}

try:
    import win32print
    import win32ui
    from PIL import ImageWin
    WIN32_OK = True
except ImportError:  # pywin32 belum terpasang / bukan Windows
    WIN32_OK = False


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            logging.exception("print_config.json rusak, memakai pengaturan bawaan")
    else:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        except OSError:
            pass
    return cfg


def prepare_image(img, paper_w, paper_h, cfg):
    """Siapkan gambar untuk area cetak paper_w x paper_h (piksel printer).
    Mengembalikan (gambar, (x0, y0, x1, y1)). Fungsi murni, mudah dites."""
    img = img.convert("RGB")

    # Strip sempit -> 2 lajur berdampingan
    if cfg.get("duplicate_strip") and img.width / img.height <= 0.4:
        doubled = Image.new("RGB", (img.width * 2, img.height), "white")
        doubled.paste(img, (0, 0))
        doubled.paste(img, (img.width, 0))
        img = doubled

    if cfg.get("auto_rotate", True) and (img.width > img.height) != (paper_w > paper_h):
        img = img.rotate(90, expand=True)

    if cfg.get("mode", "fit") == "fill":
        # Potong tengah gambar agar rasionya sama dengan kertas
        target = paper_w / paper_h
        if img.width / img.height > target:
            new_w = int(img.height * target)
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, img.height))
        else:
            new_h = int(img.width / target)
            top = (img.height - new_h) // 2
            img = img.crop((0, top, img.width, top + new_h))
        return img, (0, 0, paper_w, paper_h)

    scale = min(paper_w / img.width, paper_h / img.height)
    w, h = int(img.width * scale), int(img.height * scale)
    x0, y0 = (paper_w - w) // 2, (paper_h - h) // 2
    return img, (x0, y0, x0 + w, y0 + h)


class PrintManager:
    """Mengelola pencetakan foto ke printer sistem."""

    @staticmethod
    def list_printers():
        if not WIN32_OK:
            return []
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [p[2] for p in win32print.EnumPrinters(flags)]

    @staticmethod
    def paper_info(printer_name=""):
        """Ukuran area cetak yang dilaporkan driver (untuk memastikan kertas 4R sudah terpilih)."""
        if not WIN32_OK:
            return None
        printer = printer_name or win32print.GetDefaultPrinter()
        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer)
        try:
            px_w, px_h = hdc.GetDeviceCaps(8), hdc.GetDeviceCaps(10)       # HORZRES, VERTRES
            mm_w, mm_h = hdc.GetDeviceCaps(4), hdc.GetDeviceCaps(6)        # HORZSIZE, VERTSIZE
            dpi_x, dpi_y = hdc.GetDeviceCaps(88), hdc.GetDeviceCaps(90)    # LOGPIXELSX/Y
            return {"printer": printer, "px": (px_w, px_h), "mm": (mm_w, mm_h),
                    "inch": (round(px_w / dpi_x, 2), round(px_h / dpi_y, 2)), "dpi": (dpi_x, dpi_y)}
        finally:
            hdc.DeleteDC()

    @staticmethod
    def print_image(image_path: str) -> bool:
        if not os.path.exists(image_path):
            logging.error(f"File gambar tidak ditemukan untuk dicetak: {image_path}")
            return False

        cfg = load_config()
        try:
            if sys.platform == "win32":
                if WIN32_OK:
                    return PrintManager._print_win32(image_path, cfg)
                logging.warning(
                    "pywin32 belum terpasang (pip install pywin32). "
                    "Memakai wizard 'Print Pictures' Windows yang butuh klik manual."
                )
                os.startfile(image_path, "print")
                return True
            cmd = ["lp", "-n", str(cfg["copies"])]
            if cfg.get("printer_name"):
                cmd += ["-d", cfg["printer_name"]]
            subprocess.run(cmd + [image_path], check=True)  # macOS & Linux (CUPS)
            return True
        except Exception:
            logging.exception("Gagal mencetak foto")
            return False

    @staticmethod
    def _print_win32(image_path, cfg):
        printer = cfg.get("printer_name") or win32print.GetDefaultPrinter()
        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer)
        try:
            paper_w = hdc.GetDeviceCaps(8)    # HORZRES: lebar area cetak (piksel)
            paper_h = hdc.GetDeviceCaps(10)   # VERTRES: tinggi area cetak (piksel)
            with Image.open(image_path) as source:
                img, box = prepare_image(source, paper_w, paper_h, cfg)
            dib = ImageWin.Dib(img)
            hdc.StartDoc("PhotoBooth SIMETRI")
            for _ in range(max(1, int(cfg.get("copies", 1)))):
                hdc.StartPage()
                dib.draw(hdc.GetHandleOutput(), box)
                hdc.EndPage()
            hdc.EndDoc()
            mm_w, mm_h = hdc.GetDeviceCaps(4), hdc.GetDeviceCaps(6)
            logging.info(f"Foto dicetak ke '{printer}' ({paper_w}x{paper_h}px = {mm_w}x{mm_h}mm): {image_path}")
            return True
        finally:
            hdc.DeleteDC()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cek printer / tes cetak photobooth")
    parser.add_argument("--list", action="store_true", help="tampilkan daftar printer")
    parser.add_argument("--info", action="store_true", help="tampilkan ukuran kertas yang dilaporkan printer")
    parser.add_argument("--test", metavar="FOTO", help="cetak satu foto sebagai tes")
    args = parser.parse_args()

    if args.list:
        default = win32print.GetDefaultPrinter() if WIN32_OK else None
        for name in PrintManager.list_printers():
            print(("* " if name == default else "  ") + name)
        print("(* = printer default Windows)")
    if args.info:
        info = PrintManager.paper_info(load_config().get("printer_name", ""))
        if not info:
            print("pywin32 belum terpasang atau bukan Windows.")
        else:
            print(f"Printer      : {info['printer']}")
            print(f"Area cetak   : {info['px'][0]} x {info['px'][1]} px @ {info['dpi'][0]} dpi")
            print(f"Ukuran       : {info['mm'][0]} x {info['mm'][1]} mm  ({info['inch'][0]} x {info['inch'][1]} inci)")
            print("Target 4R    : sekitar 102 x 152 mm (4 x 6 inci). Area cetak boleh sedikit lebih kecil jika tidak borderless.")
    if args.test:
        print("Berhasil dikirim." if PrintManager.print_image(args.test) else "Gagal, lihat photobooth.log")