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

BASE_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "riwayat")
os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)

# Matikan batas maksimum ukuran gambar PIL agar tidak error DecompressionBombError
Image.MAX_IMAGE_PIXELS = None

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
    """Memotong dan memperbesar foto agar mengisi seluruh slot template secara presisi tanpa celah putih."""
    target_ratio = width / height
    image_ratio = photo.width / photo.height

    # Lakukan cropping presisi berdasarkan rasio slot
    if image_ratio > target_ratio:
        crop_width = int(photo.height * target_ratio)
        offset = (photo.width - crop_width) // 2
        cropped = photo.crop((offset, 0, offset + crop_width, photo.height))
    else:
        crop_height = int(photo.width / target_ratio)
        offset = (photo.height - crop_height) // 2
        cropped = photo.crop((0, offset, photo.width, offset + crop_height))

    # Pastikan ukuran hasil resize sesuai piksel target tanpa toleransi pembulatan
    return cropped.resize((width, height), Image.LANCZOS)


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
    def __init__(self, config, camera_index=0, cancel_event=None):
        self.config = config
        self.camera_index = camera_index
        
        # Buat folder khusus per sesi foto
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:6]
        
        # Tambahkan atribut session_dir
        self.session_dir = os.path.join(BASE_OUTPUT_DIR, f"Sesi_{timestamp}_{unique_id}")
        os.makedirs(self.session_dir, exist_ok=True)

        # Output file foto komposit
        self.output_filename = os.path.join(self.session_dir, "foto_frame.jpg")
        
        self.photos = []
        self.photo_boxes = []
        self.live_frames = []
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
        # Setting kamera ke resolusi HD & 60 FPS
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 60)  # Minta driver kamera running di 60 FPS

        photo_count = self.config["photo_count"] if photo_index is None else 1
        waiting_since = time.monotonic()

        # Standby menunggu tombol SPASI
        while True:
            if self.cancel_event.is_set():
                return False
            if time.monotonic() - waiting_since >= 120:
                return False
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                return False

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            self._frame_w, self._frame_h = w, h

            x1, y1, x2, y2 = get_group_guide_box(w, h)
            self._x1, self._y1, self._x2, self._y2 = x1, y1, x2, y2

            display_frame = self.meme_manager.apply_meme(frame.copy())
            display_frame = apply_filter(display_frame, self.filter_idx)

            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 255, 255), 2)
            filter_name = get_filter_name(self.filter_idx)
            hud = f"SPASI=Mulai | F=Filter:[{filter_name}] | ESC=Batal"
            cv2.putText(display_frame, hud, (14, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 255, 200), 1)

            cv2.imshow("Photo Booth", display_frame)

            key = cv2.waitKey(1) & 0xFF
            if key != 255:
                waiting_since = time.monotonic()
            if key == 32:  # SPASI
                break
            elif key == 27:  # ESC
                return False
            elif key in (ord('f'), ord('F')):
                self.filter_idx = (self.filter_idx + 1) % FILTER_COUNT

        captured_photos = []
        captured_boxes = []

        # Perekaman per slot foto
        for i in range(photo_count):
            delay = 3  # Hitung mundur 3 detik
            start_time = time.monotonic()
            last_record_time = 0
            photo_video_frames = []

            while time.monotonic() - start_time < delay:
                if self.cancel_event.is_set():
                    return False
                ret, frame = cap.read()
                if not ret or frame is None or frame.size == 0:
                    raise RuntimeError("Koneksi kamera terputus.")
                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]

                now = time.monotonic()
                # Perekaman 60 FPS (~0.0166 detik per frame)
                if now - last_record_time >= 0.016:
                    last_record_time = now
                    clean_frame = apply_filter(frame.copy(), self.filter_idx)
                    crop_area = clean_frame[self._y1:self._y2, self._x1:self._x2]
                    clean_rgb = cv2.cvtColor(crop_area, cv2.COLOR_BGR2RGB)
                    photo_video_frames.append(Image.fromarray(clean_rgb))

                display_frame = apply_filter(frame.copy(), self.filter_idx)
                cv2.rectangle(display_frame, (self._x1, self._y1), (self._x2, self._y2), (255, 255, 255), 2)

                elapsed = time.monotonic() - start_time
                remaining_int = int(delay - elapsed) + 1

                cv2.putText(display_frame, str(remaining_int), (w // 2 - 20, h // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 3.5, (0, 80, 255), 4)
                cv2.imshow("Photo Booth", display_frame)
                if cv2.waitKey(1) & 0xFF == 27:
                    return False

            # Jepret foto statis akhir saat countdown selesai
            ret, frame = cap.read()
            if ret:
                frame = cv2.flip(frame, 1)
                frame = apply_filter(frame, self.filter_idx)
                captured_photos.append(frame)
                captured_boxes.append((self._x1, self._y1, self._x2, self._y2))

                # Ambil 35-40 frame rekaman video 60 FPS terakhir
                recorded_clip = photo_video_frames[-40:] if photo_video_frames else []
                if photo_index is None:
                    self.live_frames.append(recorded_clip)
                else:
                    if photo_index < len(self.live_frames):
                        self.live_frames[photo_index] = recorded_clip

                # Flash
                flash = np.ones_like(frame) * 255
                cv2.imshow("Photo Booth", flash)
                cv2.waitKey(80)

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
        candidate = copy.copy(self)
        candidate.photos = list(self.photos)
        candidate.photo_boxes = list(self.photo_boxes)
        candidate.live_frames = list(self.live_frames)
        candidate.session_dir = self.session_dir
        candidate.output_filename = self.output_filename
        success = candidate.capture(photo_index=photo_index)
        return candidate if success else None

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
            # 3. Simpan Video MP4 (Menggantikan GIF)
            video_path = self._save_mp4(pil_photos, overlay=overlay)

        return self.output_filename, video_path

    def _compose_photos(self, photos, overlay=None, max_size=None):
        canvas_w, canvas_h = self.config["width"], self.config["height"]
        scale = min(1, max_size[0] / canvas_w, max_size[1] / canvas_h) if max_size else 1.0
        size = (max(1, round(canvas_w * scale)), max(1, round(canvas_h * scale)))

        # Buat Kanvas dasar transparan/hitam agar tidak muncul warna putih di pinggir slot
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        sx, sy = size[0] / canvas_w, size[1] / canvas_h

        for photo, (x, y, w, h) in zip(photos, self.config["layout"]):
            # Berikan toleransi +1 atau +2 piksel (bleed) agar foto sedikit masuk ke bawah bingkai template
            left = max(0, round(x * sx) - 1)
            top = max(0, round(y * sy) - 1)
            width = round((w + 2) * sx)
            height = round((h + 2) * sy)

            fitted = fit_group_photo(photo, width, height).convert("RGBA")
            image.paste(fitted, (left, top))

        # Tempelkan overlay PNG di atas foto
        if overlay is not None:
            frame = overlay if overlay.size == size else overlay.resize(size, Image.LANCZOS)
            image.alpha_composite(frame, (0, 0))

        return image.convert("RGB")
    
    def _save_mp4(self, pil_photos, thumb_size=(1000, 3000), overlay=None):
        """Menggabungkan frame video countdown menjadi file MP4 dengan kecepatan normal (18 FPS)."""
        mp4_path = os.path.join(self.session_dir, "animasi_live.mp4")
        try:
            gif_frames = []
            max_frames = max((len(f) for f in self.live_frames), default=0)

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

                    # Komposisi frame + overlay
                    composed = self._compose_photos(current_slot_photos, overlay, thumb_size)
                    gif_frames.append(composed)

            if not gif_frames:
                return None

            # Ambil dimensi frame pertama
            first_frame = np.array(gif_frames[0])
            height, width, _ = first_frame.shape

            # Inisialisasi VideoWriter dengan FPS normal (18 FPS) agar tidak ke-speed
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            fps = 18.0  # Kecepatan putar normal sesuai hasil tangkapan webcam
            out = cv2.VideoWriter(mp4_path, fourcc, fps, (width, height))

            for pil_img in gif_frames:
                # Konversi PIL Image (RGB) ke OpenCV Frame (BGR)
                frame_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
                out.write(frame_bgr)

            out.release()
            print(f"File MP4 Live Video kecepatan normal disimpan: {mp4_path}")
            return mp4_path

        except Exception as e:
            print(f"Error pembuatan MP4 Video: {e}")
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