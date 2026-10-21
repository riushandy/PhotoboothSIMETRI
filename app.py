import customtkinter as ctk
from print_manager import PrintManager
import tkinter as tk
from tkinter import messagebox
import os
import json
import threading
import queue
import logging
from logging.handlers import RotatingFileHandler
import cv2
import numpy as np
from PIL import Image, ImageTk

from camera_engine import PhotoBoothEngine, list_available_cameras
from preview_window import PreviewWindow
from filter_engine import apply_filter, get_filter_name, FILTER_COUNT
from idle_timer import IdleTimer

# ── Theme ──────────────────────────────────────────────────────────
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

ACCENT = "#f0627b"
ACCENT_HOVER = "#ff7f96"
BG_DARK = "#0b1220"
BG_MID = "#111c31"
BG_CARD = "#17243b"
BG_SURFACE = "#1d2d48"
BORDER = "#2b4163"
FG_TEXT = "#f4f7fb"
FG_MUTED = "#91a2bb"
GREEN = "#34d3ad"
ORANGE = "#f6b756"

CONTENT_GAP = 12
CARD_WIDTH = 214
THUMB_SIZE = (190, 148)
CONTROL_HEIGHT = 36
CORNER_RADIUS = 14


# ══════════════════════════════════════════════════════════════════
# Template Card
# ══════════════════════════════════════════════════════════════════

class TemplateCard(ctk.CTkFrame):
    def __init__(self, master, template_data, on_click, **kwargs):
        super().__init__(
            master,
            fg_color=BG_CARD,
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=BORDER,
            width=CARD_WIDTH,
            **kwargs,
        )
        self.template_data = template_data
        self.on_click = on_click
        self._hovered = False
        self._selected = False

        thumb_path = os.path.join(
            os.path.dirname(template_data.get("overlay_full_path", "")), "thumbnail.png"
        )
        if os.path.exists(thumb_path):
            pil_img = Image.open(thumb_path)
            self.thumb_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=THUMB_SIZE)
        else:
            placeholder = Image.new("RGB", (THUMB_SIZE[0], THUMB_SIZE[1]), (35, 52, 79))
            self.thumb_img = ctk.CTkImage(light_image=placeholder, dark_image=placeholder, size=THUMB_SIZE)

        self.btn = ctk.CTkButton(
            self,
            text="",
            image=self.thumb_img,
            command=lambda: on_click(template_data),
            fg_color="transparent",
            hover_color="#2a2a5a",
            width=198,
            height=156,
            corner_radius=10,
            border_width=0,
        )
        self.btn.pack(padx=8, pady=(10, 2))

        self.lbl = ctk.CTkLabel(
            self,
            text=template_data.get("name", "Template"),
            font=("Segoe UI", 13, "bold"),
            text_color=FG_TEXT,
        )
        self.lbl.pack(pady=(2, 12))

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.btn.bind("<Enter>", self._on_enter)
        self.btn.bind("<Leave>", self._on_leave)

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._sync_border()

    def _sync_border(self):
        if self._hovered or self._selected:
            self.configure(border_color=ACCENT, border_width=2)
        else:
            self.configure(border_color=BORDER, border_width=1)

    def _on_enter(self, _event=None):
        self._hovered = True
        self._sync_border()

    def _on_leave(self, _event=None):
        self._hovered = False
        self._sync_border()


# ══════════════════════════════════════════════════════════════════
# Live Camera Sidebar
# ══════════════════════════════════════════════════════════════════

class LiveCamSidebar(ctk.CTkFrame):
    PREVIEW_W = 240
    PREVIEW_H = 180

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=BG_CARD, corner_radius=CORNER_RADIUS, border_width=1, border_color=BORDER, **kwargs)
        self._stop_event = threading.Event()
        self._thread = None
        self._frame_queue = queue.Queue(maxsize=1)
        self._camera_status = "📷 Live Preview"
        self._displayed_status = None
        self._start_after = None
        self._destroyed = False
        self.current_filter = 0

        self._status_label = ctk.CTkLabel(
            self, text="📷 Live Preview", font=("Segoe UI", 13, "bold"), text_color=GREEN
        )
        self._status_label.pack(pady=(12, 4))

        self.canvas = tk.Canvas(
            self,
            width=self.PREVIEW_W,
            height=self.PREVIEW_H,
            bg="#070b13",
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.canvas.pack(padx=12, pady=4)

        self._filter_label = ctk.CTkLabel(
            self,
            text=f"Filter aktif  •  {get_filter_name(self.current_filter)}",
            font=("Segoe UI", 10),
            text_color=FG_MUTED,
            anchor="w",
        )
        self._filter_label.pack(pady=2)

        ctk.CTkButton(
            self,
            text="🔄 Ganti Filter",
            command=self._next_filter,
            fg_color=BG_SURFACE,
            hover_color="#2b4163",
            font=("Segoe UI", 11, "bold"),
            width=176,
            height=CONTROL_HEIGHT,
            corner_radius=10,
        ).pack(pady=(4, 12))
        self._poll_after = self.after(35, self._poll_frame)

    def start(self, cam_index=0):
        if self._destroyed:
            return
        if not self.stop(timeout=0):
            self._camera_status = "Menunggu kamera..."
            self._start_after = self.after(500, self.start, cam_index)
            return
        self._camera_status = "Menghubungkan kamera..."
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, args=(cam_index, self._stop_event), daemon=True
        )
        self._thread.start()

    def stop(self, timeout=1.0):
        if self._start_after is not None:
            self.after_cancel(self._start_after)
            self._start_after = None
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        if thread and thread.is_alive():
            return False
        self._thread = None
        try:
            self._frame_queue.get_nowait()
        except queue.Empty:
            pass
        return True

    def destroy(self):
        self._destroyed = True
        self.stop()
        if self._poll_after is not None:
            self.after_cancel(self._poll_after)
        super().destroy()

    def _next_filter(self):
        self.current_filter = (self.current_filter + 1) % FILTER_COUNT
        self._filter_label.configure(text=f"Filter aktif  •  {get_filter_name(self.current_filter)}")

    def _loop(self, cam_index, stop_event):
        while not stop_event.is_set():
            cap = None
            try:
                cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    cap = cv2.VideoCapture(cam_index)
                failures = 0
                while not stop_event.is_set() and cap.isOpened():
                    ret, frame = cap.read()
                    if stop_event.is_set():
                        break
                    if not ret or frame is None or frame.size == 0:
                        failures += 1
                        if failures >= 10:
                            break
                        stop_event.wait(0.1)
                        continue
                    failures = 0
                    frame = cv2.flip(frame, 1)
                    frame = apply_filter(frame, self.current_filter)
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil = Image.fromarray(frame_rgb)
                    pil.thumbnail((self.PREVIEW_W, self.PREVIEW_H), Image.LANCZOS)
                    try:
                        if self._frame_queue.full():
                            self._frame_queue.get_nowait()
                        self._frame_queue.put_nowait(pil)
                    except queue.Empty:
                        pass
                    except queue.Full:
                        pass
                    self._camera_status = "📷 Live Preview"
                    stop_event.wait(0.035)
            except Exception:
                logging.exception("Live preview gagal; mencoba sambungkan ulang")
            finally:
                if cap is not None:
                    cap.release()
            if not stop_event.is_set():
                self._camera_status = "Kamera terputus, mencoba lagi..."
                stop_event.wait(2.0)

    def _poll_frame(self):
        self._poll_after = None
        if self._destroyed:
            return
        try:
            if self._displayed_status != self._camera_status:
                status = self._camera_status
                live = status == "📷 Live Preview"
                status_color = GREEN if live else (ACCENT if "terputus" in status.lower() else ORANGE)
                self._status_label.configure(text=status, text_color=status_color)
                self._displayed_status = status
                if not live:
                    self._clear_frame_queue()
                    self._show_camera_placeholder(status)
                else:
                    self.canvas.delete("all")
                    self.canvas.image = None
            try:
                pil_img = self._frame_queue.get_nowait()
                self._update_canvas(pil_img)
            except queue.Empty:
                pass
        except Exception:
            logging.exception("Gagal menampilkan live preview")
        finally:
            if not self._destroyed:
                delay = 250 if self._stop_event.is_set() else 35
                self._poll_after = self.after(delay, self._poll_frame)

    def _clear_frame_queue(self):
        while True:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                return

    def _show_camera_placeholder(self, status):
        if not self.winfo_exists():
            return
        self.canvas.delete("all")
        self.canvas.image = None
        title = "Mencari kamera" if "menghubungkan" in status.lower() or "menunggu" in status.lower() else "Kamera terputus"
        self.canvas.create_text(
            self.PREVIEW_W // 2,
            self.PREVIEW_H // 2 - 10,
            text=f"◉\n{title}",
            fill=FG_MUTED,
            font=("Segoe UI", 13, "bold"),
            justify="center",
        )
        self.canvas.create_text(
            self.PREVIEW_W // 2,
            self.PREVIEW_H // 2 + 38,
            text="Periksa koneksi kamera",
            fill=FG_MUTED,
            font=("Segoe UI", 9),
        )

    def _update_canvas(self, pil_img):
        if not self.winfo_exists():
            return
        tk_img = ImageTk.PhotoImage(pil_img)
        self.canvas.delete("all")
        self.canvas.image = tk_img
        self.canvas.create_image(
            self.PREVIEW_W // 2, self.PREVIEW_H // 2, image=tk_img
        )

# ══════════════════════════════════════════════════════════════════
# Main App
# ══════════════════════════════════════════════════════════════════

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Photo Booth Pro 🎉")
        self._closing = False
        self._session_active = False
        self._preview_paused = False
        self._selected_template = None
        self._session_cancel = threading.Event()
        self._ui_queue = queue.Queue()
        self._ui_after = self.after(25, self._poll_ui_queue)

        self.attributes("-fullscreen", True)
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))
        self.bind("<F11>", lambda e: self.attributes("-fullscreen", True))

        self.configure(fg_color=BG_DARK)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self.templates = []
        self.cameras = []

        self._build_header()
        self._build_main()
        self._build_sidebar()
        self._build_footer()
        self._idle_timer = IdleTimer(self, 60, self._pause_idle_camera, self._resume_idle_camera)

        self._load_templates_async()

    def _run_on_ui(self, callback, *args):
        if not self._closing:
            self._ui_queue.put((callback, args))

    def _poll_ui_queue(self):
        try:
            while not self._closing:
                callback, args = self._ui_queue.get_nowait()
                try:
                    callback(*args)
                except Exception:
                    logging.exception("Callback UI gagal")
                    if self._session_active and not self._closing:
                        self._finish_session_ui()
        except queue.Empty:
            pass

        if not self._closing:
            self._ui_after = self.after(25, self._poll_ui_queue)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=0, height=78, border_width=0)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, padx=24, pady=12, sticky="w")
        ctk.CTkLabel(
            brand,
            text="PHOTO BOOTH",
            font=("Segoe UI", 11, "bold"),
            text_color=GREEN,
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="📸  Pro Studio",
            font=("Segoe UI", 21, "bold"),
            text_color=FG_TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="Capture moments. Keep them moving.",
            font=("Segoe UI", 9),
            text_color=FG_MUTED,
        ).pack(anchor="w")

        cam_frame = ctk.CTkFrame(header, fg_color="transparent")
        cam_frame.grid(row=0, column=1, padx=20, sticky="e")

        ctk.CTkLabel(
            cam_frame, text="Kamera:", font=("Segoe UI", 12), text_color=FG_MUTED
        ).pack(side="left", padx=(0, 8))

        self.cameras = list_available_cameras()
        self.camera_var = ctk.StringVar(value=self.cameras[0] if self.cameras else "Camera 0")
        self.camera_combo = ctk.CTkComboBox(
            cam_frame,
            values=self.cameras,
            variable=self.camera_var,
            width=176,
            height=CONTROL_HEIGHT,
            state="readonly",
            font=("Segoe UI", 12),
            fg_color=BG_SURFACE,
            border_color=BORDER,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            corner_radius=10,
            command=self._on_camera_change,
        )
        self.camera_combo.pack(side="left")

        ctk.CTkLabel(
            header,
            text="ESC = keluar fullscreen  •  F11 = fullscreen",
            font=("Segoe UI", 10),
            text_color=FG_MUTED,
        ).grid(row=0, column=2, padx=20)

    def _build_main(self):
        self.scroll_frame = ctk.CTkScrollableFrame(
            self,
            label_text="✨  Pilih gaya foto",
            label_font=("Segoe UI", 17, "bold"),
            label_text_color=FG_TEXT,
            fg_color=BG_DARK,
            scrollbar_button_color=ACCENT,
            scrollbar_button_hover_color=ACCENT_HOVER,
            corner_radius=CORNER_RADIUS,
        )
        self.scroll_frame.grid(row=1, column=0, padx=(16, 10), pady=CONTENT_GAP, sticky="nsew")
        self.scroll_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.scroll_frame.bind("<Configure>", self._on_template_area_resize, add="+")
        self._grid_resize_after = None
        self._grid_columns = 3

        ctk.CTkLabel(
            self.scroll_frame,
            text="Pilih template untuk mulai sesi foto",
            font=("Segoe UI", 11),
            text_color=FG_MUTED,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 8))

        self._template_hint_row = 0

        self.loading_label = ctk.CTkLabel(
            self.scroll_frame,
            text="⏳  Memuat template...",
            font=("Segoe UI", 14),
            text_color=FG_MUTED,
        )
        self.loading_label.grid(row=1, column=0, columnspan=3, pady=40)

    def _select_template_card(self, selected_card):
        for card in getattr(self, "_template_cards", []):
            card.set_selected(card is selected_card)

    def _on_template_area_resize(self, event=None):
        if self._grid_resize_after is not None:
            self.after_cancel(self._grid_resize_after)
        self._grid_resize_after = self.after(120, self._reflow_templates)

    def _reflow_templates(self):
        self._grid_resize_after = None
        if not self.templates:
            return
        width = max(self.scroll_frame.winfo_width(), 1)
        columns = max(1, min(3, width // (CARD_WIDTH + 24)))
        if columns == self._grid_columns and all(
            getattr(card, "grid_info", None) for card in getattr(self, "_template_cards", [])
        ):
            return
        self._grid_columns = columns
        for idx, card in enumerate(getattr(self, "_template_cards", [])):
            card.grid(row=idx // columns + 1, column=idx % columns, padx=10, pady=10, sticky="n")
        for column in range(3):
            self.scroll_frame.grid_columnconfigure(column, weight=1 if column < columns else 0)

    def _clear_template_cards(self):
        for card in getattr(self, "_template_cards", []):
            card.destroy()
        self._template_cards = []

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=0, width=286)
        self.sidebar.grid(row=1, column=1, padx=0, pady=0, sticky="ns")
        self.sidebar.grid_propagate(False)

        ctk.CTkLabel(
            self.sidebar,
            text="YOUR STUDIO",
            font=("Segoe UI", 10, "bold"),
            text_color=GREEN,
        ).pack(anchor="w", padx=18, pady=(18, 0))
        ctk.CTkLabel(
            self.sidebar,
            text="Preview & controls",
            font=("Segoe UI", 16, "bold"),
            text_color=FG_TEXT,
        ).pack(anchor="w", padx=18, pady=(2, 10))

        self.live_cam = LiveCamSidebar(self.sidebar)
        self.live_cam.pack(padx=14, pady=(0, 10), fill="x")

        # Tombol Konfirmasi Mulai Sesi
        self.btn_confirm_session = ctk.CTkButton(
            self.sidebar,
            text="🎬 Mulai Sesi Foto",
            command=self._start_confirmed_session,
            fg_color=GREEN,
            hover_color="#57e0bd",
            text_color=BG_DARK,
            font=("Segoe UI", 13, "bold"),
            height=42,
            corner_radius=10,
            state="disabled",
        )
        self.btn_confirm_session.pack(padx=14, pady=(0, 10), fill="x")

        info = ctk.CTkFrame(self.sidebar, fg_color=BG_CARD, corner_radius=CORNER_RADIUS, border_width=1, border_color=BORDER)
        info.pack(padx=14, pady=0, fill="x")

        ctk.CTkLabel(
            info,
            text="⌨ Pintasan cepat",
            font=("Segoe UI", 12, "bold"),
            text_color=FG_TEXT,
        ).pack(anchor="w", padx=14, pady=(13, 9))

        controls = [
            ("SPACE", "Mulai sesi foto"),
            ("F", "Ganti filter"),
            ("M", "Sticker mode ON/OFF"),
            ("S", "Ganti sticker"),
            ("ESC", "Batal / keluar"),
        ]
        for key, desc in controls:
            row = ctk.CTkFrame(info, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(
                row,
                text=key,
                font=("Consolas", 10, "bold"),
                text_color=ORANGE,
                fg_color=BG_SURFACE,
                corner_radius=6,
                width=52,
                height=24,
            ).pack(side="left")
            ctk.CTkLabel(
                row,
                text=desc,
                font=("Segoe UI", 10),
                text_color=FG_MUTED,
                anchor="w",
            ).pack(side="left", padx=9)

        ctk.CTkLabel(info, text="", height=8).pack()
        self._start_live_cam()

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color=BG_MID, corner_radius=0, height=38, border_width=0)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        footer.grid_propagate(False)

        ctk.CTkLabel(
            footer,
            text="Photo Booth Pro  •  Simpan otomatis  •  Upload aman via QR",
            font=("Segoe UI", 10),
            text_color=FG_MUTED,
        ).pack(side="left", padx=20)

        self.status_label = ctk.CTkLabel(
            footer,
            text="●  Siap",
            font=("Segoe UI", 10, "bold"),
            text_color=GREEN,
        )
        self.status_label.pack(side="right", padx=20)

    def _load_templates_async(self):
        def _worker():
            templates = self._load_templates()
            self._run_on_ui(self._populate_grid, templates)

        threading.Thread(target=_worker, daemon=True).start()

    def _load_templates(self):
        templates = []
        base_dir = "assets/templates"
        if not os.path.exists(base_dir):
            return []
        for folder in os.listdir(base_dir):
            path = os.path.join(base_dir, folder)
            config_path = os.path.join(path, "config.json")
            if os.path.isdir(path) and os.path.exists(config_path):
                try:
                    with open(config_path, "r") as f:
                        data = json.load(f)
                    data["overlay_full_path"] = os.path.join(path, data.get("overlay_path", "overlay.png"))
                    templates.append(data)
                except Exception as e:
                    print(f"Error loading {folder}: {e}")
        return templates

    def _populate_grid(self, templates):
        if self.loading_label.winfo_exists():
            self.loading_label.destroy()
        self._clear_template_cards()
        self.templates = templates

        if not templates:
            ctk.CTkLabel(
                self.scroll_frame,
                text="⚠️  Tidak ada template ditemukan di assets/templates/",
                font=("Segoe UI", 13),
                text_color=ORANGE,
            ).grid(row=1, column=0, columnspan=3, pady=40)
            return

        self._template_cards = [
            TemplateCard(self.scroll_frame, tmpl, self.on_template_select)
            for tmpl in templates
        ]
        self._grid_columns = 0
        self._reflow_templates()
        self.status_label.configure(text=f"●  {len(templates)} template siap")

    def _start_live_cam(self):
        self._preview_paused = False
        idx = self._cam_index()
        self.live_cam.start(cam_index=idx)

    def _pause_idle_camera(self):
        if self._closing or self._session_active:
            return
        self._preview_paused = True
        self.live_cam.stop()
        self.live_cam._camera_status = "Siap — gerakkan mouse untuk preview"
        self.live_cam.canvas.delete("all")
        self.live_cam.canvas.image = None

    def _resume_idle_camera(self):
        if self._preview_paused and not self._closing and not self._session_active:
            self._start_live_cam()

    def _cam_index(self):
        selected = self.camera_var.get()
        try:
            return int(selected.split(" ")[1])
        except Exception:
            return 0

    def _on_camera_change(self, _value=None):
        if not self._session_active:
            self.live_cam.start(cam_index=self._cam_index())

    def on_template_select(self, template_data):
        if self._session_active or self._closing:
            return

        self._selected_template = template_data
        for card in getattr(self, "_template_cards", []):
            card.set_selected(card.template_data is template_data)

        template_name = template_data.get("name", "Template")
        self.btn_confirm_session.configure(
            state="normal",
            text=f"🎬 Mulai Sesi ({template_name})"
        )
        self.status_label.configure(
            text=f"● Template '{template_name}' dipilih. Klik 'Mulai Sesi'",
            text_color=GREEN
        )

    def _start_confirmed_session(self):
        if not getattr(self, "_selected_template", None) or self._session_active or self._closing:
            return

        template_data = self._selected_template
        self._session_active = True
        self._session_cancel.clear()
        self._set_ui_enabled(False)
        self.btn_confirm_session.configure(state="disabled", text="🎬 Sesi foto berjalan...")
        self.status_label.configure(text="🎬 Sesi foto sedang berjalan...", text_color=ORANGE)

        if not self.live_cam.stop():
            self._finish_session_ui()
            messagebox.showerror("Kamera Sibuk", "Kamera belum selesai merespons. Tunggu sebentar lalu coba lagi.")
            return

        threading.Thread(
            target=self._run_session,
            args=(template_data, self._cam_index(), self.live_cam.current_filter),
            daemon=True,
        ).start()

    def _run_session(self, *args, **kwargs):
        """Menjalankan sesi foto di background thread."""
        try:
            # Pembacaan aman untuk config template
            config = getattr(self, "config", getattr(self, "current_config", getattr(self, "selected_config", None)))
            
            # Pembacaan aman untuk camera_index (default: 0)
            cam_idx = getattr(self, "camera_index", getattr(self, "cam_index", getattr(self, "selected_camera", 0)))
            
            # 1. Inisialisasi engine kamera
            engine = PhotoBoothEngine(config, camera_index=cam_idx)
            
            # 2. Jalankan pengambilan foto
            success = engine.capture()
            if not success:
                return  # Pengguna membatalkan sesi (ESC)

            # 3. Proses penggabungan foto komposit dan video MP4
            composite_path, mp4_path = engine.process()

            if not composite_path or not os.path.exists(composite_path):
                raise RuntimeError("Gagal menghasilkan foto komposit.")

            # 4. Tampilkan halaman preview
            self._run_on_ui(self._show_preview, engine, composite_path, mp4_path)

        except Exception as e:
            err_msg = str(e)
            logging.error(f"Sesi foto gagal: {err_msg}")
            self._run_on_ui(
                lambda msg=err_msg: messagebox.showerror("Sesi Foto Gagal", f"Sesi tidak dapat diselesaikan:\n{msg}\n\nSilakan coba lagi.")
            )

    def _show_preview(self, engine, composite_path, gif_path):
        if getattr(self, "_active_preview_window", None) is not None and self._active_preview_window.root.winfo_exists():
            return

        def do_upload(upload_path=None, include_gif=False):
            upload_path = upload_path or composite_path
            upload_type = "foto + GIF (ZIP)" if include_gif else ("GIF" if upload_path == gif_path else "foto")
            self.status_label.configure(
                text=f"📤 Mengupload {upload_type}...", text_color=ORANGE
            )

            def on_upload_done(link):
                self._run_on_ui(handle_upload_done, link)

            def handle_upload_done(link):
                if link:
                    qr_path = engine.upload_manager.generate_qr(link)
                    reupload = lambda: do_upload(upload_path, include_gif=include_gif)
                    return_to_result = lambda: self._show_preview(
                        engine, composite_path, gif_path
                    )
                    self.status_label.configure(text="✅ Upload selesai!", text_color=GREEN)
                    engine.upload_manager.show_qr_window(
                        qr_path,
                        link,
                        40,
                        reupload,
                        return_to_result,
                        download_label="Foto + GIF — buka ZIP untuk mengambil keduanya" if include_gif else upload_type,
                        timeout_callback=self._finish_session,
                        wait=False,
                    )
                else:
                    self.status_label.configure(text="❌ Upload gagal", text_color=ACCENT)
                    self.after(0, messagebox.showerror, "Upload Gagal",
                               "Tidak bisa upload.\nPastikan laptop terhubung internet.\n\nFoto disimpan lokal.")
                    self._finish_session()

            engine.upload_async(upload_path, on_done=on_upload_done, gif_path=gif_path if include_gif else None)

        def do_print(image_path):
            self.status_label.configure(text="🖨 Mengirim ke printer...", text_color=ORANGE)
            success = PrintManager.print_image(image_path)
            if success:
                self.status_label.configure(text="✅ Foto dikirim ke printer!", text_color=GREEN)
                messagebox.showinfo("Cetak Foto", "Foto berhasil dikirim ke printer.")
            else:
                self.status_label.configure(text="❌ Gagal mencetak foto", text_color=ACCENT)
                messagebox.showerror("Cetak Gagal", "Gagal mengirim foto ke printer.\nPeriksa koneksi printer.")

        def do_retake():
            self._session_active = True
            self._session_cancel.clear()
            self._set_ui_enabled(False)
            if not self.live_cam.stop():
                self._finish_session_ui()
                messagebox.showerror("Kamera Sibuk", "Tunggu sebentar lalu pilih template lagi.")
                return
            self.status_label.configure(text="🔄 Retake...", text_color=ORANGE)
            threading.Thread(target=self._run_session, daemon=True).start()

        def do_retake_one(photo_index):
            self._session_cancel.clear()
            self._session_active = True
            self._set_ui_enabled(False)
            if not self.live_cam.stop():
                messagebox.showerror("Kamera Sibuk", "Tunggu sebentar lalu coba ulang foto lagi.")
                self._run_on_ui(self._show_retake_preview, engine, composite_path, gif_path, "Kamera masih sibuk")
                return
            self.status_label.configure(text=f"🔄 Mengulang foto {photo_index + 1}...", text_color=ORANGE)
            threading.Thread(
                target=self._run_single_retake,
                args=(engine, photo_index, composite_path, gif_path),
                daemon=True,
            ).start()

        def do_save():
            self.status_label.configure(
                text=f"💾 Tersimpan: {composite_path}", text_color=GREEN
            )
            self._finish_session()

        preview = PreviewWindow(
            composite_path,
            do_upload,
            do_retake,
            do_save,
            print_callback=do_print,
            gif_path=gif_path,
            bundle_callback=lambda: do_upload(composite_path, include_gif=True),
            photo_previews=[engine.get_photo_preview(i) for i in range(len(engine.photos))],
            retake_one_callback=do_retake_one,
        )
        self._active_preview_window = preview
        preview.show(wait=False)

    def _run_single_retake(self, engine, photo_index, composite_path, gif_path):
        try:
            candidate = engine.retake_photo(photo_index)
            if candidate is not None and not self._session_cancel.is_set():
                result = candidate.process()
                if not result:
                    raise RuntimeError("Hasil foto ulang tidak dapat diproses.")
                self._run_on_ui(self._show_retake_preview, candidate, *result, f"✅ Foto {photo_index + 1} sudah diperbarui")
                return
        except Exception as e:
            logging.exception("Retake satu foto gagal")
            self._run_on_ui(messagebox.showerror, "Retake Gagal", f"Foto sebelumnya tetap tersedia.\n{e}")
        self._run_on_ui(self._show_retake_preview, engine, composite_path, gif_path, "Hasil sebelumnya tetap tersedia")

    def _show_retake_preview(self, engine, composite_path, gif_path, status):
        if self._closing:
            return
        self.status_label.configure(text=status, text_color=GREEN)
        self._active_preview_window = None
        self._show_preview(engine, composite_path, gif_path)

    def _finish_session(self):
        self._run_on_ui(self._finish_session_ui)

    def _finish_session_ui(self):
        if self._closing:
            return
        self._session_active = False
        self._preview_paused = False
        self._selected_template = None
        self._idle_timer.touch()
        self._set_ui_enabled(True)
        
        self.btn_confirm_session.configure(
            state="disabled",
            text="🎬 Mulai Sesi Foto"
        )
        
        for card in getattr(self, "_template_cards", []):
            card.set_selected(False)

        self._start_live_cam()
        if self.status_label.cget("text_color") == ORANGE:
            self.status_label.configure(text="✅ Siap", text_color=GREEN)

    def _set_ui_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.camera_combo.configure(state="readonly" if enabled else "disabled")
        for widget in self.scroll_frame.winfo_children():
            if isinstance(widget, TemplateCard):
                widget.btn.configure(state=state)

    def on_closing(self):
        self._closing = True
        self._session_cancel.set()
        self._idle_timer.cancel()
        self.after_cancel(self._ui_after)
        self.live_cam.stop()
        self.destroy()

    def report_callback_exception(self, exc_type, exc_value, traceback):
        logging.error("Callback Tk gagal", exc_info=(exc_type, exc_value, traceback))


if __name__ == "__main__":
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photobooth.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()