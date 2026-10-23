import cv2
import time
import os
import threading
import numpy as np
import uuid
import logging
import copy
from collections import deque
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

# ── Pengaturan countdown & video (ubah di sini) ──────────────────
COUNTDOWN_SECONDS = 10   # hitung mundur per foto
VIDEO_SECONDS = 5        # durasi video = N detik TERAKHIR sebelum jepret (harus < COUNTDOWN_SECONDS)
VIDEO_FPS = 30           # fps video hasil; kamera harus mampu mengirim fps ini (naikkan ke 60 bila kamera kuat)


def create_output_path(extension=".jpg"):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    unique_id = uuid.uuid4().hex[:8]
    return os.path.join(OUTPUT_DIR, f"hasil_photobooth_{timestamp}_{unique_id}{extension}")


def get_group_guide_box(frame_w, frame_h, margin_ratio=0.035):
    margin_x = int(frame_w * margin_ratio)
    margin_y = int(frame_h * margin_ratio)
    return margin_x, margin_y, frame_w - margin_x, frame_h - margin_y


def fit_group_photo(photo, width, height, resample=Image.LANCZOS):
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
    return cropped.resize((width, height), resample)


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
        frame_interval = 1.0 / VIDEO_FPS

        # Perekaman per slot foto
        for i in range(photo_count):
            start_time = time.monotonic()
            last_record_time = 0.0
            clip = deque()  # (timestamp, jpeg_bytes) -> hanya VIDEO_SECONDS terakhir

            while time.monotonic() - start_time < COUNTDOWN_SECONDS:
                if self.cancel_event.is_set():
                    return False

                ret, frame = cap.read()
                if not ret or frame is None or frame.size == 0:
                    raise RuntimeError("Koneksi kamera terputus.")

                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                now = time.monotonic()

                # Rekam sesuai VIDEO_FPS; disimpan sebagai JPEG supaya hemat memori
                if now - last_record_time >= frame_interval * 0.9:
                    last_record_time = now
                    clean_frame = apply_filter(frame.copy(), self.filter_idx)
                    crop_area = clean_frame[self._y1:self._y2, self._x1:self._x2]
                    ok, buf = cv2.imencode(".jpg", crop_area, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    if ok:
                        clip.append((now, buf.tobytes()))
                        cutoff = now - VIDEO_SECONDS
                        while clip and clip[0][0] < cutoff:
                            clip.popleft()

                display_frame = apply_filter(frame.copy(), self.filter_idx)
                cv2.rectangle(display_frame, (self._x1, self._y1), (self._x2, self._y2), (255, 255, 255), 2)

                elapsed = time.monotonic() - start_time
                remaining_int = int(COUNTDOWN_SECONDS - elapsed) + 1
                cv2.putText(display_frame, str(remaining_int), (w // 2 - 40, h // 2),
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

                recorded_clip = list(clip)
                if photo_index is None:
                    self.live_frames.append(recorded_clip)
                elif photo_index < len(self.live_frames):
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
        """Simpan foto template (JPG), semua foto raw, dan video MP4."""
        if not self.photos:
            return None

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

        # 1. Foto dengan template (JPG kualitas tinggi)
        final_image = self._compose_photos(pil_photos, overlay)
        final_image.save(
            self.output_filename,
            format="JPEG", quality=98, subsampling=0, dpi=(300, 300),
        )
        print(f"Foto template disimpan: {self.output_filename}")

        # 2. Foto raw (tanpa template) -> foto_raw_1.jpg, foto_raw_2.jpg, ...
        self._save_raw_photos()

        # 3. Video MP4
        self.video_path = self._save_mp4(pil_photos, overlay=overlay)
        return self.output_filename, self.video_path

    def _save_raw_photos(self):
        self.raw_paths = []
        for i, frame in enumerate(self.photos):
            path = os.path.join(self.session_dir, f"foto_raw_{i + 1}.jpg")
            Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).save(
                path, format="JPEG", quality=97, subsampling=0
            )
            self.raw_paths.append(path)
        return self.raw_paths

    def session_files(self):
        """Semua file hasil sesi: foto raw + foto template + video."""
        files = list(getattr(self, "raw_paths", []))
        files.append(self.output_filename)
        video = getattr(self, "video_path", None)
        if video:
            files.append(video)
        return [f for f in files if f and os.path.isfile(f)]

    def _compose_photos(self, photos, overlay=None, max_size=None, fast=False):
        canvas_w, canvas_h = self.config["width"], self.config["height"]
        scale = min(1, max_size[0] / canvas_w, max_size[1] / canvas_h) if max_size else 1.0
        size = (max(1, round(canvas_w * scale)), max(1, round(canvas_h * scale)))

        # Buat Kanvas dasar transparan/hitam agar tidak muncul warna putih di pinggir slot
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        sx, sy = size[0] / canvas_w, size[1] / canvas_h

        resample = Image.BILINEAR if fast else Image.LANCZOS
        for photo, (x, y, w, h) in zip(photos, self.config["layout"]):
            # Berikan toleransi +1 atau +2 piksel (bleed) agar foto sedikit masuk ke bawah bingkai template
            left = max(0, round(x * sx) - 1)
            top = max(0, round(y * sy) - 1)
            width = round((w + 2) * sx)
            height = round((h + 2) * sy)

            fitted = fit_group_photo(photo, width, height, resample).convert("RGBA")
            image.paste(fitted, (left, top))

        # Tempelkan overlay PNG di atas foto
        if overlay is not None:
            frame = overlay if overlay.size == size else overlay.resize(size, Image.LANCZOS)
            image.alpha_composite(frame, (0, 0))

        return image.convert("RGB")
    
    @staticmethod
    def _decode_jpeg(data):
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))

    def _save_mp4(self, pil_photos, overlay=None, max_size=(1080, 1920)):
        """Video = VIDEO_SECONDS detik terakhir sebelum tiap jepretan, diputar real-time
        (di-resample berdasarkan timestamp, jadi durasi tetap benar walau fps kamera berbeda)."""
        mp4_path = os.path.join(self.session_dir, "video_sesi.mp4")
        try:
            layout = self.config["layout"]
            clips = list(self.live_frames)
            if not any(clips):
                return None

            canvas_w, canvas_h = self.config["width"], self.config["height"]
            scale = min(1, max_size[0] / canvas_w, max_size[1] / canvas_h)
            size = (max(1, round(canvas_w * scale)), max(1, round(canvas_h * scale)))
            out_w, out_h = size[0] // 2 * 2, size[1] // 2 * 2  # encoder butuh ukuran genap
            if overlay is not None and overlay.size != size:
                overlay = overlay.resize(size, Image.LANCZOS)  # sekali saja, bukan per frame

            writer = None
            for codec in ("avc1", "mp4v"):  # H.264 bila tersedia, kalau tidak MPEG-4
                writer = cv2.VideoWriter(
                    mp4_path, cv2.VideoWriter_fourcc(*codec), float(VIDEO_FPS), (out_w, out_h)
                )
                if writer.isOpened():
                    break
                writer.release()
                writer = None
            if writer is None:
                raise RuntimeError("Encoder video tidak tersedia.")

            total = int(VIDEO_SECONDS * VIDEO_FPS)
            pointers = [0] * len(layout)
            cache = {}  # slot -> (index, PIL image)

            for k in range(total):
                slot_images = []
                for slot in range(len(layout)):
                    clip = clips[slot] if slot < len(clips) else None
                    if not clip:
                        slot_images.append(pil_photos[min(slot, len(pil_photos) - 1)])
                        continue
                    t = clip[-1][0] - VIDEO_SECONDS + k / VIDEO_FPS
                    idx = pointers[slot]
                    while idx + 1 < len(clip) and clip[idx + 1][0] <= t:
                        idx += 1
                    pointers[slot] = idx
                    if slot not in cache or cache[slot][0] != idx:
                        cache[slot] = (idx, self._decode_jpeg(clip[idx][1]))
                    slot_images.append(cache[slot][1])

                composed = self._compose_photos(slot_images, overlay, max_size, fast=True)
                if composed.size != (out_w, out_h):
                    composed = composed.resize((out_w, out_h), Image.BILINEAR)
                writer.write(cv2.cvtColor(np.array(composed), cv2.COLOR_RGB2BGR))

            writer.release()
            print(f"Video disimpan: {mp4_path} ({VIDEO_SECONDS} detik, {VIDEO_FPS} fps)")
            return mp4_path
        except Exception:
            logging.exception("Pembuatan video MP4 gagal")
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

    def upload_session_async(self, on_done=None):
        """Upload semua file sesi (raw + template + video) ke satu folder Google Drive."""
        def _worker():
            link = None
            try:
                link = self.upload_manager.upload_session(
                    self.session_files(), os.path.basename(self.session_dir)
                )
            except Exception:
                logging.exception("Gagal mengupload folder sesi")
            if on_done:
                on_done(link)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t