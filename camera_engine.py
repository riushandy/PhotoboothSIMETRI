import cv2
import time
import os
import threading
import numpy as np
import uuid
import logging
import copy
from datetime import datetime
from PIL import Image, ImageFilter

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

from meme_manager import MemeManager
from upload_manager import UploadManager
from filter_engine import apply_filter, get_filter_name, FILTER_COUNT


OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "riwayat")
CAMERA_IDLE_SECONDS = 120


def create_output_path(extension=".jpg"):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    unique_id = uuid.uuid4().hex[:8]
    return os.path.join(OUTPUT_DIR, f"hasil_photobooth_{timestamp}_{unique_id}{extension}")


def get_group_guide_box(frame_w, frame_h, margin_ratio=0.035):
    margin_x = int(frame_w * margin_ratio)
    margin_y = int(frame_h * margin_ratio)
    return margin_x, margin_y, frame_w - margin_x, frame_h - margin_y


def fit_group_photo(photo, width, height):
    background = photo.copy()
    target_ratio = width / height
    image_ratio = background.width / background.height

    if image_ratio > target_ratio:
        crop_width = int(background.height * target_ratio)
        offset = (background.width - crop_width) // 2
        background = background.crop((offset, 0, offset + crop_width, background.height))
    else:
        crop_height = int(background.width / target_ratio)
        offset = (background.height - crop_height) // 2
        background = background.crop((0, offset, background.width, offset + crop_height))

    background = background.resize((width, height), Image.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(radius=max(8, min(width, height) // 35)))

    foreground = photo.copy()
    foreground.thumbnail((width, height), Image.LANCZOS)
    paste_x = (width - foreground.width) // 2
    paste_y = (height - foreground.height) // 2
    background.paste(foreground, (paste_x, paste_y))
    return background


def list_available_cameras(max_tests=5):
    available_cameras = []
    for i in range(max_tests):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            available_cameras.append(f"Camera {i}")
        cap.release()
    if not available_cameras:
        available_cameras.append("Camera 0 (Default)")
    return available_cameras


def _beep(freq=880, duration_ms=120):
    if HAS_WINSOUND:
        try:
            winsound.Beep(freq, duration_ms)
        except Exception:
            pass


class PhotoBoothEngine:
    def __init__(self, config, camera_index=0, output_filename=None, cancel_event=None):
        self.config = config
        self.camera_index = camera_index
        self.output_filename = output_filename or create_output_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.output_filename)), exist_ok=True)
        self.photos = []
        self.photo_boxes = []
        self.live_frames = []  # Buffer menyimpan rekaman live photo saat countdown
        self.cancel_event = cancel_event or threading.Event()
        self.meme_manager = MemeManager()
        self.upload_manager = UploadManager()

        self.filter_idx = 0

        self._x1 = self._y1 = self._x2 = self._y2 = 0
        self._frame_w = self._frame_h = 0

    def capture(self, photo_index=None):
        if photo_index is not None and not 0 <= photo_index < len(self.photos):
            raise IndexError("Nomor foto yang akan diulang tidak valid.")
        cap = None
        if photo_index is None:
            self.photos = []
            self.photo_boxes = []
            self.live_frames = []
        try:
            if self.cancel_event.is_set():
                return False
            cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                raise RuntimeError("Kamera tidak dapat diakses. Periksa koneksi kamera.")
            return self._capture(cap, photo_index=photo_index)
        finally:
            if cap is not None:
                cap.release()
            cv2.destroyAllWindows()

    def _capture(self, cap, photo_index=None):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        photo_count = self.config["photo_count"] if photo_index is None else 1

        print("Tekan SPASI untuk mulai | F = filter | M = sticker mode | ESC = batal")
        waiting_since = time.monotonic()

        while True:
            if self.cancel_event.is_set():
                return False
            if time.monotonic() - waiting_since >= CAMERA_IDLE_SECONDS:
                logging.info("Sesi kamera dibatalkan setelah %s detik tidak digunakan", CAMERA_IDLE_SECONDS)
                return False
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                logging.warning("Kamera terputus saat menunggu; kembali ke halaman awal")
                return False

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            self._frame_w, self._frame_h = w, h

            x1, y1, x2, y2 = get_group_guide_box(w, h)
            self._x1, self._y1, self._x2, self._y2 = x1, y1, x2, y2

            display_frame = self.meme_manager.apply_meme(frame.copy())
            display_frame = apply_filter(display_frame, self.filter_idx)

            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 255, 255), 2)
            cv2.putText(display_frame, "Area Foto Lebar", (x1, max(28, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            filter_name = get_filter_name(self.filter_idx)
            sticker_status = "ON" if self.meme_manager.meme_mode else "OFF"
            hud = f"SPASI=Mulai | F=Filter:[{filter_name}] | M=Sticker:{sticker_status} | ESC=Batal"
            if photo_index is not None:
                cv2.putText(display_frame, f"Ulang Foto {photo_index + 1} - SPASI=Mulai",
                            (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 100), 2)
            cv2.putText(display_frame, hud, (14, h - 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 255, 200), 1)

            cv2.imshow("Photo Booth", display_frame)

            key = cv2.waitKey(1) & 0xFF
            if key != 255:
                waiting_since = time.monotonic()
            if key == 32:
                break
            elif key == 27:
                return False
            elif key in (ord('m'), ord('M')):
                self.meme_manager.toggle_mode()
            elif key in (ord('f'), ord('F')):
                self.filter_idx = (self.filter_idx + 1) % FILTER_COUNT
            elif key in (ord('s'), ord('S')):
                self.meme_manager.cycle_sticker()

        captured_photos = []
        captured_boxes = []
        x1, y1, x2, y2 = self._x1, self._y1, self._x2, self._y2

        for i in range(photo_count):
            delay = 6
            start_time = time.monotonic()
            last_record_time = 0
            photo_live_frames = []

            while time.monotonic() - start_time < delay:
                if self.cancel_event.is_set():
                    return False
                ret, frame = cap.read()
                if not ret or frame is None or frame.size == 0:
                    raise RuntimeError("Koneksi kamera terputus saat mengambil foto. Silakan coba lagi.")
                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]

                # --- 1. Rekam Frame Murni untuk GIF Live Photo ---
                now = time.monotonic()
                if now - last_record_time >= 0.1:  # Rekam tiap 100ms (~10 fps)
                    last_record_time = now
                    clean_frame = self.meme_manager.apply_meme(frame.copy())
                    clean_frame = apply_filter(clean_frame, self.filter_idx)
                    
                    # Crop area foto sesuai guide box
                    crop_area = clean_frame[y1:y2, x1:x2]
                    clean_rgb = cv2.cvtColor(crop_area, cv2.COLOR_BGR2RGB)
                    photo_live_frames.append(Image.fromarray(clean_rgb))

                # --- 2. Tampilkan Frame UI dengan Angka Countdown ---
                display_frame = self.meme_manager.apply_meme(frame.copy())
                display_frame = apply_filter(display_frame, self.filter_idx)
                cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 255, 255), 2)

                elapsed = time.monotonic() - start_time
                remaining_int = int(delay - elapsed) + 1
                pulse = 3.0 + 1.5 * abs(np.sin(elapsed * np.pi))
                font_scale = pulse
                thickness = max(4, int(pulse * 1.2))
                text = str(remaining_int)
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
                tx = (w - tw) // 2
                ty = (h + th) // 2

                cv2.putText(display_frame, text, (tx + 3, ty + 3),
                            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness + 2)
                cv2.putText(display_frame, text, (tx, ty),
                            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 80, 255), thickness)

                counter_txt = f"Foto {i + 1}/{photo_count}" if photo_index is None else f"Ulang Foto {photo_index + 1}"
                cv2.putText(display_frame, counter_txt, (14, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 100), 2)

                cv2.imshow("Photo Booth", display_frame)
                if cv2.waitKey(1) & 0xFF == 27:
                    return False

                if elapsed < 0.05 or (1.0 < elapsed < 1.05) or (2.0 < elapsed < 2.05):
                    threading.Thread(target=_beep, daemon=True).start()

            # Ambil foto hasil akhir
            if self.cancel_event.is_set():
                return False
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                raise RuntimeError("Kamera gagal mengambil foto. Silakan coba lagi.")
            if ret:
                frame = cv2.flip(frame, 1)

                if self.meme_manager.meme_mode:
                    frame = self.meme_manager.apply_meme(frame)

                frame = apply_filter(frame, self.filter_idx)
                captured_photos.append(frame)
                captured_boxes.append(get_group_guide_box(frame.shape[1], frame.shape[0]))

                # Simpan rekaman live photo dari foto ini (ambil ~15 frame terakhir/1.5 detik)
                if photo_index is None:
                    self.live_frames.append(photo_live_frames[-15:])
                else:
                    if photo_index < len(self.live_frames):
                        self.live_frames[photo_index] = photo_live_frames[-15:]

                flash = np.ones_like(frame) * 255
                cv2.imshow("Photo Booth", flash)
                cv2.waitKey(80)

                threading.Thread(target=lambda: _beep(1200, 80), daemon=True).start()

                cv2.imshow("Photo Booth", frame)
                cv2.waitKey(400)

        if len(captured_photos) != photo_count:
            return False
        if photo_index is None:
            self.photos = captured_photos
            self.photo_boxes = captured_boxes
        else:
            self.photos[photo_index] = captured_photos[0]
            self.photo_boxes[photo_index] = captured_boxes[0]
        return True

    def retake_photo(self, photo_index):
        if not 0 <= photo_index < len(self.photos):
            raise IndexError("Nomor foto yang akan diulang tidak valid.")
        candidate = copy.copy(self)
        candidate.photos = self.photos.copy()
        candidate.live_frames = self.live_frames.copy()
        candidate.photo_boxes = [self._photo_box(i) for i in range(len(self.photos))]
        candidate.meme_manager = copy.copy(self.meme_manager)
        candidate.output_filename = create_output_path()
        if candidate.capture(photo_index=photo_index):
            return candidate
        return None

    def _photo_box(self, index):
        boxes = getattr(self, "photo_boxes", [])
        if index < len(boxes):
            return boxes[index]
        return self._x1, self._y1, self._x2, self._y2

    def get_photo_preview(self, index):
        photo = Image.fromarray(cv2.cvtColor(self.photos[index], cv2.COLOR_BGR2RGB))
        x1, y1, x2, y2 = self._photo_box(index)
        return photo.crop((max(0, x1), max(0, y1), min(photo.width, x2), min(photo.height, y2)))

    def process(self, on_complete=None):
        """Simpan JPG + GIF otomatis dalam resolusi tinggi sebelum preview."""
        if not self.photos:
            return

        canvas_w = self.config["width"]
        canvas_h = self.config["height"]
        layout = self.config["layout"]

        pil_photos = []
        for i in range(min(len(self.photos), len(layout))):
            pil_photos.append(self.get_photo_preview(i))

        # Overlay
        overlay = None
        overlay_path = self.config.get("overlay_full_path")
        if overlay_path and os.path.exists(overlay_path):
            with Image.open(overlay_path) as source:
                overlay = source.convert("RGBA")
            if overlay.size != (canvas_w, canvas_h):
                overlay = overlay.resize((canvas_w, canvas_h), Image.LANCZOS)

        # 1. Simpan Foto Komposit JPG dalam resolusi maksimal (Quality 98, DPI 300)
        final_image = self._compose_photos(pil_photos, overlay)
        final_image.save(
            self.output_filename,
            format="JPEG",
            quality=98,        # Menaikkan kualitas kompresi JPG dari 92 ke 98
            subsampling=0,     # Mencegah kompresi warna (chroma subsampling 4:4:4)
            dpi=(300, 300)     # Mengatur resolusi cetak ke 300 DPI (standar cetak foto)
        )
        print(f"Foto komposit resolusi tinggi disimpan: {self.output_filename}")

        # 2. Simpan GIF/Boomerang
        gif_path = None
        if pil_photos:
            gif_path = self._save_gif(pil_photos, overlay=overlay)

        return self.output_filename, gif_path

    def _compose_photos(self, photos, overlay=None, max_size=None):
        """Susun foto pada slot template dengan pemrosesan resolusi tinggi."""
        canvas_w, canvas_h = self.config["width"], self.config["height"]
        
        # Jika max_size diberikan (seperti pada preview/GIF), sesuaikan skalanya.
        # Jika tidak (untuk file JPG asli), gunakan ukuran resolusi canvas penuh.
        scale = min(1, max_size[0] / canvas_w, max_size[1] / canvas_h) if max_size else 1.0
        size = (max(1, round(canvas_w * scale)), max(1, round(canvas_h * scale)))
        
        image = Image.new("RGB", size, "white")
        sx, sy = size[0] / canvas_w, size[1] / canvas_h
        
        for photo, (x, y, w, h) in zip(photos, self.config["layout"]):
            left, top = round(x * sx), round(y * sy)
            width = max(1, round((x + w) * sx) - left)
            height = max(1, round((y + h) * sy) - top)
            # Menempelkan foto dengan resampling LANCZOS kualitas tinggi
            image.paste(fit_group_photo(photo, width, height), (left, top))
            
        if overlay is not None:
            frame = overlay if overlay.size == size else overlay.resize(size, Image.LANCZOS)
            image.paste(frame, (0, 0), frame)
            
        return image
    
    def _save_gif(self, pil_photos, thumb_size=(800, 1200), overlay=None):
        """Buat GIF Live Photo resolusi tinggi dengan template/overlay yang tetap diam (statis)."""
        gif_path = os.path.splitext(self.output_filename)[0] + ".gif"

        try:
            gif_frames = []
            max_frames = max((len(f) for f in self.live_frames), default=0)

            # Jika ada rekaman live frames saat countdown
            if max_frames > 0:
                for frame_idx in range(max_frames):
                    current_slot_photos = []
                    for slot_idx in range(len(self.config["layout"])):
                        if slot_idx < len(self.live_frames) and self.live_frames[slot_idx]:
                            frames_list = self.live_frames[slot_idx]
                            img = frames_list[min(frame_idx, len(frames_list) - 1)]
                        else:
                            img = pil_photos[min(slot_idx, len(pil_photos) - 1)]
                        current_slot_photos.append(img)

                    # Buat frame gabungan slot + overlay dengan resolusi lebih tinggi
                    composed_frame = self._compose_photos(current_slot_photos, overlay, thumb_size)
                    gif_frames.append(composed_frame)

            # Fallback jika tidak ada rekaman live photo
            if not gif_frames:
                gif_frames = [
                    self._compose_photos(pil_photos[i:] + pil_photos[:i], overlay, thumb_size)
                    for i in range(len(pil_photos))
                ]

            # Konversi setiap frame ke Palette 256 warna dengan kualitas warna lebih bersih
            converted_frames = [
                frame.convert("P", palette=Image.ADAPTIVE, colors=256)
                for frame in gif_frames
            ]

            # Simpan GIF dengan kualitas tinggi
            converted_frames[0].save(
                gif_path,
                save_all=True,
                append_images=converted_frames[1:],
                duration=110,  # Waktu antar frame (~9 FPS)
                loop=0,
                optimize=False,
                disposal=2,
            )
            print(f"GIF Live Photo resolusi tinggi berhasil disimpan: {gif_path}")
            return gif_path

        except Exception as e:
            print(f"Gagal menyimpan GIF Live Photo: {e}")
            return None
        
    def upload_async(self, filepath, on_done=None, gif_path=None):
        def _worker():
            link = None
            try:
                upload_path = self.upload_manager.create_bundle(filepath, gif_path) if gif_path else filepath
                link = self.upload_manager.upload_file(upload_path)
            except Exception:
                logging.exception("Gagal menyiapkan atau mengupload hasil foto")
            if on_done:
                on_done(link)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t