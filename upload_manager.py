import os
import requests
import qrcode
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import threading
import time
import zipfile
import math


from drive_manager import get_drive_manager


class UploadManager:
    UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"
    MAX_RETRIES = 2

    def __init__(self):
        self.storage_note = "Link aktif ±1 jam via tmpfiles.org"

    # ─────────────────────────────────────────────────────────────
    # UPLOAD
    # ─────────────────────────────────────────────────────────────

    def upload_file(self, filepath):
        """Upload ke tmpfiles.org dengan fallback ke catbox.moe jika DNS/koneksi gagal."""
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)

        # Utama: Google Drive. Jika belum di-setup/gagal, lanjut ke layanan cadangan.
        drive = get_drive_manager()
        if drive.is_ready():
            print(f"[Upload] {filename} ({file_size // 1024} KB) ke Google Drive...")
            link = drive.upload_file(filepath)
            if link:
                self.storage_note = "Tersimpan di Google Drive"
                print(f"Upload Berhasil (Drive)! Link: {link}")
                return link
            print("Upload Drive gagal, mencoba layanan cadangan...")
        self.storage_note = "Link aktif ±1 jam via tmpfiles.org"

        # Opsi 1: Coba upload ke tmpfiles.org
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                print(f"[Upload Attempt {attempt}] {filename} ({file_size // 1024} KB) ke tmpfiles.org...")
                with open(filepath, "rb") as f:
                    response = requests.post(
                        "https://tmpfiles.org/api/v1/upload",
                        files={"file": (filename, f)},
                        timeout=30,
                    )

                if response.status_code == 200:
                    data = response.json()
                    download_url = data["data"]["url"]
                    print(f"Upload Berhasil (tmpfiles)! Link: {download_url}")
                    return download_url
            except Exception as e:
                print(f"Upload tmpfiles error (attempt {attempt}): {e}")
            time.sleep(1)

        # Opsi 2: Fallback ke Catbox.moe jika tmpfiles gagal/tidak terjangkau
        print("Mencoba upload ke server cadangan (Catbox.moe)...")
        try:
            with open(filepath, "rb") as f:
                response = requests.post(
                    "https://catbox.moe/user/api.php",
                    data={"reqtype": "fileupload"},
                    files={"fileToUpload": (filename, f)},
                    timeout=45,
                )
            if response.status_code == 200 and response.text.startswith("http"):
                download_url = response.text.strip()
                print(f"Upload Berhasil (Catbox)! Link: {download_url}")
                return download_url
        except Exception as e:
            print(f"Upload Catbox error: {e}")

        print("Semua server upload gagal.")
        return None
    # ─────────────────────────────────────────────────────────────
    # QR CODE
    # ─────────────────────────────────────────────────────────────

    def generate_qr(self, url, output_path="qr_share_internet.png"):
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(output_path)
        return output_path

    # ─────────────────────────────────────────────────────────────
    # QR WINDOW (Tkinter — lebih cantik dari OpenCV)
    # ─────────────────────────────────────────────────────────────

    def show_qr_window(
        self, qr_path, url, timeout=40, reupload_callback=None, close_callback=None,
        download_label="Foto",
        timeout_callback=None, wait=True,
    ):
        """Tampilkan QR dengan countdown visual, tombol Tutup & Upload Ulang."""
        with Image.open(qr_path) as source:
            qr_img_raw = source.resize((280, 280), Image.LANCZOS)
        root = tk.Toplevel()
        root.title(f"Scan untuk Download {download_label}")
        root.configure(bg="#0f3460")
        root.resizable(False, False)
        root.grab_set()

        # ── Title ──────────────────────────────────────────────
        tk.Label(
            root,
            text="📸 SCAN UNTUK DOWNLOAD",
            font=("Segoe UI", 18, "bold"),
            bg="#0f3460",
            fg="#e94560",
        ).pack(pady=(18, 2))

        tk.Label(
            root, text=download_label, font=("Segoe UI", 12, "bold"),
            bg="#0f3460", fg="#00d4aa",
        ).pack(pady=4)

        tk.Label(
            root, text="Scan QR, lalu simpan/unduh dari halaman yang terbuka.",
            font=("Segoe UI", 10), bg="#0f3460", fg="#a8a8b3",
        ).pack(padx=16)

        tk.Label(
            root,
            text=self.storage_note,
            font=("Segoe UI", 10),
            bg="#0f3460",
            fg="#a8a8b3",
        ).pack()

        # ── QR Image ───────────────────────────────────────────
        qr_tk = ImageTk.PhotoImage(qr_img_raw, master=root)

        qr_frame = tk.Label(
            root, image=qr_tk, bg="white", relief="flat", bd=8
        )
        qr_frame.image = qr_tk  # prevent GC
        qr_frame.pack(padx=28, pady=14)

        # ── URL Label ──────────────────────────────────────────
        url_display = url if len(url) < 52 else url[:49] + "…"
        tk.Label(
            root,
            text=url_display,
            font=("Consolas", 9),
            bg="#0f3460",
            fg="#00d4aa",
            wraplength=320,
        ).pack(pady=(0, 6))

        # ── Countdown ─────────────────────────────────────────
        count_var = tk.StringVar(value=f"⏱ {timeout} detik")
        count_label = tk.Label(
            root,
            textvariable=count_var,
            font=("Segoe UI", 14),
            bg="#0f3460",
            fg="#f5a623",
        )
        count_label.pack(pady=4)

        # ── Buttons ────────────────────────────────────────────
        btn_frame = tk.Frame(root, bg="#0f3460")
        btn_frame.pack(pady=14)

        timer_id = None
        closed = False

        def cancel_timer():
            nonlocal timer_id
            if timer_id is not None:
                root.after_cancel(timer_id)
                timer_id = None

        def on_destroy(event):
            nonlocal closed
            if event.widget is root:
                closed = True
                cancel_timer()

        root.bind("<Destroy>", on_destroy, add="+")

        def destroy_window():
            nonlocal closed
            if closed:
                return False
            closed = True
            cancel_timer()
            root.destroy()
            return True

        def close_window():
            if not destroy_window():
                return
            if close_callback:
                close_callback()

        def on_close():
            close_window()

        def on_reupload():
            if not destroy_window():
                return
            if reupload_callback:
                reupload_callback()

        btn_close = tk.Button(
            btn_frame,
            text="✖  Tutup",
            font=("Segoe UI", 11, "bold"),
            bg="#e94560",
            fg="white",
            activebackground="#c73652",
            relief="flat",
            cursor="hand2",
            padx=18,
            pady=8,
            command=on_close,
        )
        btn_close.grid(row=0, column=0, padx=10)

        if reupload_callback:
            btn_reup = tk.Button(
                btn_frame,
                text="🔄  Upload Ulang",
                font=("Segoe UI", 11, "bold"),
                bg="#16213e",
                fg="#00d4aa",
                activebackground="#00d4aa",
                activeforeground="#16213e",
                relief="flat",
                cursor="hand2",
                padx=18,
                pady=8,
                command=on_reupload,
            )
            btn_reup.grid(row=0, column=1, padx=10)

        # ── Countdown timer loop ───────────────────────────────
        deadline = time.monotonic() + timeout

        def _tick():
            nonlocal timer_id
            timer_id = None
            if closed:
                return
            remaining = max(0, math.ceil(deadline - time.monotonic()))
            if remaining <= 0:
                if destroy_window():
                    callback = timeout_callback or close_callback
                    if callback:
                        callback()
                return
            count_var.set(f"⏱ {remaining} detik")
            timer_id = root.after(1000, _tick)

        timer_id = root.after(1000, _tick)
        root.protocol("WM_DELETE_WINDOW", close_window)
        if wait:
            root.wait_window()
        return root
    def create_bundle(self, photo_path, gif_path):
        """Paket satu sesi saja agar satu link dapat mengunduh foto dan GIF."""
        if not os.path.isfile(photo_path) or not os.path.isfile(gif_path):
            raise FileNotFoundError("Foto atau GIF untuk paket download tidak ditemukan.")
        bundle_path = os.path.splitext(photo_path)[0] + ".zip"
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.write(photo_path, arcname="foto.jpg")
            bundle.write(gif_path, arcname="animasi.gif")
        return bundle_path

