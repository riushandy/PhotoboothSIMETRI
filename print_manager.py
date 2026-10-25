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
PRINT_RENDER_DPI = 300

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
    # Untuk kertas yang dipotong manual (printer tetap mengira A4): cetak hanya di area kecil
    # pada halaman, di posisi kertas potongan itu berada. Atur lalu kalibrasi dengan
    #   python print_manager.py --calibrate
    "area": {
        "enabled": False,
        "width_mm": 102,        # 4 inci
        "height_mm": 152,       # 6 inci
        "align_x": "center",    # left | center | right  (sisi tempat kertas bersandar di baki)
        "align_y": "top",       # top | center | bottom
        "offset_x_mm": 0,       # geser halus: + = ke kanan
        "offset_y_mm": 0,       # geser halus: + = ke bawah
    },
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


def area_box(paper_w, paper_h, dpi_x, dpi_y, cfg):
    """Kotak area cetak (x0, y0, x1, y1) pada halaman. Tanpa 'area' aktif = seluruh halaman."""
    a = cfg.get("area") or {}
    if not a.get("enabled"):
        return (0, 0, paper_w, paper_h)
    w = min(paper_w, round(a.get("width_mm", 102) / 25.4 * dpi_x))
    h = min(paper_h, round(a.get("height_mm", 152) / 25.4 * dpi_y))
    x0 = {"left": 0, "center": (paper_w - w) // 2, "right": paper_w - w}.get(a.get("align_x", "center"), (paper_w - w) // 2)
    y0 = {"top": 0, "center": (paper_h - h) // 2, "bottom": paper_h - h}.get(a.get("align_y", "top"), 0)
    x0 += round(a.get("offset_x_mm", 0) / 25.4 * dpi_x)
    y0 += round(a.get("offset_y_mm", 0) / 25.4 * dpi_y)
    x0, y0 = max(0, min(x0, paper_w - w)), max(0, min(y0, paper_h - h))
    return (x0, y0, x0 + w, y0 + h)


def resize_for_print(img, box, dpi_x, dpi_y, render_dpi=PRINT_RENDER_DPI):
    """Downsample to the physical print size at render_dpi to limit DIB memory use."""
    if dpi_x <= 0 or dpi_y <= 0 or render_dpi <= 0:
        raise ValueError("Resolusi printer dan DPI render harus lebih besar dari nol.")

    x0, y0, x1, y1 = box
    target_w = max(1, round((x1 - x0) * render_dpi / dpi_x))
    target_h = max(1, round((y1 - y0) * render_dpi / dpi_y))
    if img.width <= target_w and img.height <= target_h:
        return img
    return img.resize((target_w, target_h), Image.Resampling.LANCZOS)


def calibration_image(paper_w, paper_h, box):
    """Halaman uji: bingkai area cetak + silang di tengahnya + garis tepi halaman."""
    from PIL import ImageDraw
    img = Image.new("RGB", (paper_w, paper_h), "white")
    d = ImageDraw.Draw(img)
    t = max(3, paper_w // 400)
    d.rectangle((0, 0, paper_w - 1, paper_h - 1), outline="red", width=t)
    x0, y0, x1, y1 = box
    d.rectangle(box, outline="black", width=t)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    d.line((x0, cy, x1, cy), fill="black", width=t)
    d.line((cx, y0, cx, y1), fill="black", width=t)
    d.text((x0 + 10, y0 + 10), "AREA CETAK", fill="black")
    return img


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
    def print_image(image_path: str, printer_name=None, copies=None) -> bool:
        """printer_name/copies (opsional) menimpa print_config.json. Dipakai UI tablet
        agar printer dipilih manual oleh operator."""
        if not os.path.exists(image_path):
            logging.error(f"File gambar tidak ditemukan untuk dicetak: {image_path}")
            return False

        cfg = load_config()
        if printer_name:
            cfg["printer_name"] = printer_name
        if copies:
            cfg["copies"] = int(copies)
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
    def print_calibration(printer_name=""):
        """Cetak halaman uji untuk menyetel posisi area (lihat --calibrate)."""
        cfg = load_config()
        printer = printer_name or cfg.get("printer_name") or win32print.GetDefaultPrinter()
        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer)
        try:
            pw, ph = hdc.GetDeviceCaps(8), hdc.GetDeviceCaps(10)
            dpi_x, dpi_y = hdc.GetDeviceCaps(88), hdc.GetDeviceCaps(90)
            render_w = max(1, round(pw * PRINT_RENDER_DPI / dpi_x))
            render_h = max(1, round(ph * PRINT_RENDER_DPI / dpi_y))
            box = area_box(pw, ph, dpi_x, dpi_y, cfg)
            render_box = (
                round(box[0] * render_w / pw),
                round(box[1] * render_h / ph),
                round(box[2] * render_w / pw),
                round(box[3] * render_h / ph),
            )
            dib = ImageWin.Dib(calibration_image(render_w, render_h, render_box))
            hdc.StartDoc("Kalibrasi PhotoBooth")
            hdc.StartPage()
            dib.draw(hdc.GetHandleOutput(), (0, 0, pw, ph))
            hdc.EndPage()
            hdc.EndDoc()
            return True
        finally:
            hdc.DeleteDC()

    @staticmethod
    def _print_win32(image_path, cfg):
        printer = cfg.get("printer_name") or win32print.GetDefaultPrinter()
        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer)
        try:
            paper_w = hdc.GetDeviceCaps(8)    # HORZRES: lebar area cetak (piksel)
            paper_h = hdc.GetDeviceCaps(10)   # VERTRES: tinggi area cetak (piksel)
            dpi_x, dpi_y = hdc.GetDeviceCaps(88), hdc.GetDeviceCaps(90)
            ax0, ay0, ax1, ay1 = area_box(paper_w, paper_h, dpi_x, dpi_y, cfg)
            with Image.open(image_path) as source:
                img, box = prepare_image(source, ax1 - ax0, ay1 - ay0, cfg)
            box = (box[0] + ax0, box[1] + ay0, box[2] + ax0, box[3] + ay0)
            original_size = img.size
            img = resize_for_print(img, box, dpi_x, dpi_y)
            dib = ImageWin.Dib(img)
            hdc.StartDoc("PhotoBooth SIMETRI")
            for _ in range(max(1, int(cfg.get("copies", 1)))):
                hdc.StartPage()
                dib.draw(hdc.GetHandleOutput(), box)
                hdc.EndPage()
            hdc.EndDoc()
            mm_w, mm_h = hdc.GetDeviceCaps(4), hdc.GetDeviceCaps(6)
            logging.info(
                f"Foto dicetak ke '{printer}' ({paper_w}x{paper_h}px = {mm_w}x{mm_h}mm, "
                f"gambar {original_size[0]}x{original_size[1]} -> {img.width}x{img.height}px "
                f"@ maksimal {PRINT_RENDER_DPI} DPI): {image_path}"
            )
            return True
        finally:
            hdc.DeleteDC()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cek printer / tes cetak photobooth")
    parser.add_argument("--list", action="store_true", help="tampilkan daftar printer")
    parser.add_argument("--info", action="store_true", help="tampilkan ukuran kertas yang dilaporkan printer")
    parser.add_argument("--test", metavar="FOTO", help="cetak satu foto sebagai tes")
    parser.add_argument("--calibrate", action="store_true", help="cetak halaman uji posisi area cetak")
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
    if args.calibrate:
        print("Halaman uji dikirim." if PrintManager.print_calibration(load_config().get("printer_name", "")) else "Gagal.")
    if args.test:
        print("Berhasil dikirim." if PrintManager.print_image(args.test) else "Gagal, lihat photobooth.log")