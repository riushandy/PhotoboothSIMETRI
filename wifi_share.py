import http.server
import socketserver
import socket
import threading
import os
import qrcode
import tkinter as tk
from PIL import Image, ImageTk

PORT = 8000


class _DirectoryHandler(http.server.SimpleHTTPRequestHandler):
    """Handler yang serve dari direktori tertentu tanpa os.chdir()."""

    def __init__(self, *args, directory=None, **kwargs):
        self._serve_dir = directory or os.getcwd()
        super().__init__(*args, directory=self._serve_dir, **kwargs)

    def log_message(self, format, *args):
        pass  # Silent


class WifiSharer:
    def __init__(self, output_dir="."):
        self.output_dir = os.path.abspath(output_dir)
        self.ip = self._get_local_ip()
        self.port = PORT
        self.server_thread = None
        self.is_running = False
        self.httpd = None

    def _get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    def start_server(self):
        if self.is_running:
            return

        handler = lambda *args, **kwargs: _DirectoryHandler(
            *args, directory=self.output_dir, **kwargs
        )

        try:
            socketserver.TCPServer.allow_reuse_address = True
            self.httpd = socketserver.TCPServer(("", self.port), handler)
            self.server_thread = threading.Thread(
                target=self.httpd.serve_forever, daemon=True
            )
            self.server_thread.start()
            self.is_running = True
            print(f"Local server: http://{self.ip}:{self.port}")
        except OSError as e:
            print(f"Server warning: {e}")
            self.is_running = True

    def generate_qr(self, filename):
        if not self.is_running:
            self.start_server()

        basename = os.path.basename(filename)
        url = f"http://{self.ip}:{self.port}/{basename}"
        print(f"Local Link: {url}")

        qr = qrcode.QRCode(box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        qr_path = "qr_share_local.png"
        img.save(qr_path)
        return qr_path, url

    def show_qr_window(self, qr_path, url, timeout=20):
        """Tampilkan QR code local share via tkinter."""
        root = tk.Toplevel()
        root.title("Share via Wi-Fi Lokal")
        root.configure(bg="#1a1a2e")
        root.resizable(False, False)
        root.grab_set()

        tk.Label(
            root,
            text="📡 SCAN UNTUK DOWNLOAD (Wi-Fi Lokal)",
            font=("Segoe UI", 14, "bold"),
            bg="#1a1a2e",
            fg="#00d4aa",
        ).pack(pady=(16, 2))

        tk.Label(
            root,
            text=f"Harus satu jaringan Wi-Fi  •  IP: {self.ip}",
            font=("Segoe UI", 9),
            bg="#1a1a2e",
            fg="#a8a8b3",
        ).pack()

        qr_img_raw = Image.open(qr_path).resize((240, 240), Image.LANCZOS)
        qr_tk = ImageTk.PhotoImage(qr_img_raw)
        qr_label = tk.Label(root, image=qr_tk, bg="white", bd=6, relief="flat")
        qr_label.image = qr_tk
        qr_label.pack(padx=24, pady=12)

        count_var = tk.StringVar(value=f"⏱ {timeout} detik")
        tk.Label(
            root,
            textvariable=count_var,
            font=("Segoe UI", 12),
            bg="#1a1a2e",
            fg="#f5a623",
        ).pack(pady=4)

        tk.Button(
            root,
            text="✖  Tutup",
            font=("Segoe UI", 11, "bold"),
            bg="#e94560",
            fg="white",
            relief="flat",
            cursor="hand2",
            padx=18,
            pady=8,
            command=root.destroy,
        ).pack(pady=12)

        remaining = [timeout]

        def _tick():
            if remaining[0] <= 0:
                root.destroy()
                return
            remaining[0] -= 1
            count_var.set(f"⏱ {remaining[0]} detik")
            root.after(1000, _tick)

        root.after(1000, _tick)
        root.wait_window()
