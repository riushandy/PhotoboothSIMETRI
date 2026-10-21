import os
import tkinter as tk

from PIL import Image, ImageTk, ImageSequence
from idle_timer import IdleTimer


BG = "#0b1220"
SURFACE = "#111c31"
CARD = "#17243b"
SURFACE_HOVER = "#2b4163"
BORDER = "#2b4163"
TEXT = "#f4f7fb"
MUTED = "#91a2bb"
ACCENT = "#f0627b"
GREEN = "#34d3ad"
ORANGE = "#f6b756"


class PreviewWindow:
    """Window preview hasil sesi dengan aksi upload, retake, print, dan simpan."""

    def __init__(
        self,
        composite_path: str,
        upload_callback,
        retake_callback,
        save_callback,
        print_callback=None,
        gif_path: str = None,
        bundle_callback=None,
        photo_previews=None,
        retake_one_callback=None,
    ):
        self.composite_path = composite_path
        self.gif_path = gif_path
        self.upload_callback = upload_callback
        self.retake_callback = retake_callback
        self.save_callback = save_callback
        self.print_callback = print_callback
        self.bundle_callback = bundle_callback
        self.photo_previews = photo_previews or []
        self.retake_one_callback = retake_one_callback
        self._retake_images = []
        self.result = None
        self.gif_frames = []
        self.frame_delay = 220
        self.frame_index = 0
        self._animation_after = None
        self._idle_timer = None
        self._closed = False
        self._focus_widgets = []

    def show(self, wait=True):
        """Buka preview; wait=False menjaga antrean UI aplikasi tetap berjalan."""
        self.root = tk.Toplevel()
        self.root.title("Preview Hasil Foto")
        self.root.configure(bg=BG)
        self.root.transient(self.root.master)
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self.root.bind("<Escape>", lambda _event: self._on_save())

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self._compact_layout = screen_w < 1180 or screen_h < 760
        margin = 32
        window_w = min(1120, max(600, screen_w - margin))
        window_h = min(820, max(500, screen_h - margin))
        self.root.geometry(f"{window_w}x{window_h}+{max(0, (screen_w-window_w)//2)}+{max(0, (screen_h-window_h)//2)}")
        self.root.minsize(min(600, screen_w - margin), min(500, screen_h - margin))
        self.root.maxsize(screen_w - margin, screen_h - margin)
        self.root.resizable(True, True)
        self.root.grab_set()

        self._preview_size = (
            (max(160, min(240, (screen_w - 100) // 2)), max(180, min(280, screen_h - 470)))
            if self._compact_layout
            else (min(320, screen_w // 4), min(370, screen_h - 450))
        )

        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        content = tk.Frame(outer, bg=BG)
        content.pack(fill="both", expand=True)
        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", side="bottom")

        self.root.bind("<Tab>", self._focus_next, add="+")
        self.root.bind("<Shift-Tab>", self._focus_previous, add="+")

        header = tk.Frame(content, bg=BG)
        header.pack(fill="x", padx=24, pady=(18, 2))
        tk.Label(header, text="YOUR MOMENT", font=("Segoe UI", 10, "bold"),
                 bg=BG, fg=GREEN).pack(anchor="w")
        tk.Label(header, text="📸  Hasil foto kamu", font=("Segoe UI", 20, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        folder_name = os.path.basename(os.path.dirname(os.path.abspath(self.composite_path)))
        tk.Label(header, text=f"✓  Tersimpan otomatis di folder {folder_name}",
                 font=("Segoe UI", 10), bg=BG, fg=GREEN).pack(anchor="w", pady=(2, 0))

        try:
            self._load_previews()
        except Exception:
            self._close()
            raise

        preview_frame = tk.Frame(content, bg=BG)
        preview_frame.pack(fill="both", expand=True, padx=24, pady=(12, 4))
        panel_side = "top" if self._compact_layout else "left"
        panel_pad = {"pady": (0, 8)} if self._compact_layout else {"padx": (0, 8)}

        photo_panel = tk.Frame(preview_frame, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        photo_panel.pack(side=panel_side, anchor="n", **panel_pad)
        tk.Label(photo_panel, text="FINAL PHOTO", font=("Segoe UI", 10, "bold"),
                 bg=CARD, fg=GREEN).pack(anchor="w", padx=12, pady=(9, 5))
        tk.Label(photo_panel, bg=CARD, image=self.photo_tk).pack(padx=10, pady=(0, 10))

        if self.gif_frames:
            gif_panel = tk.Frame(preview_frame, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
            gif_panel.pack(side=panel_side, anchor="n", **panel_pad)
            tk.Label(gif_panel, text="ANIMATED FRAME", font=("Segoe UI", 10, "bold"),
                     bg=CARD, fg=ORANGE).pack(anchor="w", padx=12, pady=(9, 5))
            self.gif_label = tk.Label(gif_panel, bg=CARD, image=self.gif_frames[0])
            self.gif_label.pack(padx=10, pady=(0, 10))
            self._animation_after = self.root.after(self.frame_delay, self._animate_preview)

        if self.photo_previews and self.retake_one_callback:
            self._build_retake_panel(preview_frame, panel_side)

        btn_frame = tk.Frame(footer, bg=BG)
        btn_frame.pack(fill="x", padx=24, pady=(8, 14))
        btn_style = {
            "font": ("Segoe UI", 10, "bold"), "relief": "flat", "cursor": "hand2",
            "padx": 10, "pady": 8, "bd": 0, "highlightthickness": 2,
            "highlightbackground": BG, "highlightcolor": ACCENT,
        }

        # Menghitung total tombol aktif untuk menentukan jumlah kolom grid
        actions = [
            ("📸  Download Foto", SURFACE, GREEN, self._on_upload_photo),
        ]
        if self.gif_path and os.path.exists(self.gif_path):
            actions.append(("🎬  Download Video MP4", SURFACE, ORANGE, self._on_upload_gif))
        if self.print_callback:
            actions.append(("🖨  Cetak Foto", SURFACE, ACCENT, self._on_print))
        actions.extend([
            ("🔄  Ulang Semua", SURFACE, ORANGE, self._on_retake),
            ("✓  Selesai", SURFACE, MUTED, self._on_save),
        ])

        total_columns = len(actions)

        if self.bundle_callback and self.gif_path and os.path.exists(self.gif_path):
            bundle = tk.Button(btn_frame, text="📦  Download Foto + GIF (1 QR)",
                               bg=GREEN, fg=BG, activebackground="#57e0bd", activeforeground=BG,
                               command=self._on_upload_bundle, **btn_style)
            bundle.grid(row=0, column=0, columnspan=total_columns, padx=4, pady=(0, 8), sticky="ew")

        for column, (label, bg, fg, callback) in enumerate(actions):
            button = tk.Button(btn_frame, text=label, bg=bg, fg=fg,
                               activebackground=fg, activeforeground=BG,
                               command=callback, **btn_style)
            button.grid(row=1, column=column, padx=4, sticky="ew")
            btn_frame.grid_columnconfigure(column, weight=1)
            self._focus_widgets.append(button)
            button.bind("<Return>", lambda _event, target=button: target.invoke())
            button.bind("<space>", lambda _event, target=button: target.invoke())

        if self.bundle_callback and self.gif_path and os.path.exists(self.gif_path):
            self._focus_widgets.insert(0, bundle)
            bundle.bind("<Return>", lambda _event: bundle.invoke())
            bundle.bind("<space>", lambda _event: bundle.invoke())

        self.root.protocol("WM_DELETE_WINDOW", self._on_save)
        self._idle_timer = IdleTimer(self.root, 120, self._on_save)
        if self._focus_widgets:
            self._focus_widgets[0].focus_set()
        if wait:
            self.root.wait_window()
        return self.result

    def _focus_next(self, _event=None):
        self._move_focus(1)
        return "break"

    def _focus_previous(self, _event=None):
        self._move_focus(-1)
        return "break"

    def _move_focus(self, direction):
        if not self._focus_widgets:
            return
        try:
            index = self._focus_widgets.index(self.root.focus_get())
        except ValueError:
            index = -1 if direction > 0 else 0
        self._focus_widgets[(index + direction) % len(self._focus_widgets)].focus_set()

    def _build_retake_panel(self, master, side="left"):
        panel = tk.Frame(master, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        panel.pack(side=side, padx=(8, 0) if side == "left" else 0,
                   pady=(0, 8) if side == "top" else 0, anchor="n")
        tk.Label(panel, text="RETAKE ONE", font=("Segoe UI", 10, "bold"),
                 bg=CARD, fg=ORANGE).pack(anchor="w", padx=12, pady=(9, 6))
        scroll = tk.Frame(panel, bg=CARD)
        scroll.pack(padx=7, pady=(0, 8))
        height = min(300, max(80, 78 * len(self.photo_previews)))
        canvas = tk.Canvas(scroll, width=128, height=height, bg=CARD, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(scroll, orient="vertical", command=canvas.yview,
                                 troughcolor=SURFACE, bg=BORDER, activebackground=ORANGE)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left")
        scrollbar.pack(side="right", fill="y")
        content = tk.Frame(canvas, bg=CARD)
        canvas.create_window((0, 0), window=content, anchor="nw", width=128)
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        for index, photo in enumerate(self.photo_previews):
            thumb = photo.copy()
            thumb.thumbnail((104, 54), Image.LANCZOS)
            image = ImageTk.PhotoImage(thumb)
            self._retake_images.append(image)
            button = tk.Button(
                content, image=image, compound="top", text=f"↻ Ulang Foto {index + 1}",
                command=lambda idx=index: self._on_retake_one(idx),
                font=("Segoe UI", 9, "bold"), bg=SURFACE, fg=ORANGE,
                activebackground=SURFACE_HOVER, activeforeground=TEXT,
                relief="flat", cursor="hand2", padx=4, pady=4, highlightthickness=0,
            )
            button.pack(fill="x", padx=3, pady=3)
            button.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"))

    def _load_previews(self):
        """Memuat preview foto komposit dan animasi secara aman."""
        try:
            # 1. Preview Foto Utama (JPG)
            if self.composite_path and os.path.exists(self.composite_path):
                with Image.open(self.composite_path) as img:
                    img_ratio = img.width / img.height
                    # Resize proporsional untuk tampilan preview di layar
                    target_h = 500
                    target_w = int(target_h * img_ratio)
                    preview_img = img.resize((target_w, target_h), Image.LANCZOS)
                    self.composite_photo = ImageTk.PhotoImage(preview_img)
                    if hasattr(self, "composite_label"):
                        self.composite_label.configure(image=self.composite_photo)

            # 2. Preview GIF / Video MP4
            if self.gif_path and os.path.exists(self.gif_path):
                # Jika formatnya GIF, buka dengan Pillow
                if self.gif_path.lower().endswith(".gif"):
                    with Image.open(self.gif_path) as gif:
                        self.gif_frames = []
                        try:
                            while True:
                                frame = gif.copy().convert("RGBA")
                                frame.thumbnail((300, 300), Image.LANCZOS)
                                self.gif_frames.append(ImageTk.PhotoImage(frame))
                                gif.seek(gif.tell() + 1)
                        except EOFError:
                            pass
                    if self.gif_frames and hasattr(self, "gif_label"):
                        self._animate_gif(0)
                
                # Jika formatnya MP4, tampilkan status info tanpa membukanya via Pillow
                elif self.gif_path.lower().endswith(".mp4"):
                    if hasattr(self, "gif_label"):
                        self.gif_label.configure(
                            text="🎬 Video MP4 Live berhasil dibuat!\nDapat diunduh via tombol Download Video.",
                            font=("Segoe UI", 11),
                            fg="#00d4aa"
                        )

        except Exception as e:
            print(f"Peringatan saat memuat preview: {e}")

    def _animate_preview(self):
        self._animation_after = None
        if self._closed or not self.root.winfo_exists() or len(self.gif_frames) < 2:
            return
        self.frame_index = (self.frame_index + 1) % len(self.gif_frames)
        self.gif_label.configure(image=self.gif_frames[self.frame_index])
        self._animation_after = self.root.after(self.frame_delay, self._animate_preview)

    def _cancel_timers(self):
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        if self._animation_after is not None:
            self.root.after_cancel(self._animation_after)
            self._animation_after = None

    def _on_destroy(self, event):
        if event.widget is self.root and not self._closed:
            self._closed = True
            self._cancel_timers()

    def _close(self):
        if self._closed:
            return False
        self._closed = True
        self._cancel_timers()
        self.root.destroy()
        return True

    def _on_upload_photo(self):
        if self._closed:
            return
        self.result = "upload_photo"
        self._close()
        self.upload_callback(self.composite_path)

    def _on_upload_bundle(self):
        if self._closed:
            return
        self.result = "upload_bundle"
        self._close()
        self.bundle_callback()

    def _on_upload_gif(self):
        if self._closed:
            return
        self.result = "upload_gif"
        self._close()
        self.upload_callback(self.gif_path)

    def _on_print(self):
        if self._closed or not self.print_callback:
            return
        self.print_callback(self.composite_path)

    def _on_retake(self):
        if self._closed:
            return
        self.result = "retake"
        self._close()
        self.retake_callback()

    def _on_retake_one(self, photo_index):
        if self._closed or not self.retake_one_callback or not 0 <= photo_index < len(self.photo_previews):
            return
        self.result = "retake_one"
        self._close()
        self.retake_one_callback(photo_index)

    def _on_save(self):
        if self._closed:
            return
        self.result = "save"
        self._close()
        if self.save_callback:
            self.save_callback()