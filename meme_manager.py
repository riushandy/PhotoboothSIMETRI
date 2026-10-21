import cv2
import os
import numpy as np
from PIL import Image

# ── Sticker Definitions ──────────────────────────────────────────
# Setiap sticker punya: key, asset path, placement_mode
# placement_mode: "face" = tengah wajah, "eyes" = area mata, "top" = atas kepala, "mouth" = area mulut

STICKERS = [
    {
        "key": "glasses",
        "path": "assets/meme/glasses.png",
        "placement": "eyes",
        "scale": 1.3,
        "y_offset_ratio": -0.05,
    },
    {
        "key": "hat",
        "path": "assets/meme/hat.png",
        "placement": "top",
        "scale": 1.4,
        "y_offset_ratio": -0.55,
    },
    {
        "key": "mustache",
        "path": "assets/meme/mustache.png",
        "placement": "mouth",
        "scale": 0.9,
        "y_offset_ratio": 0.3,
    },
    {
        "key": "cat_ears",
        "path": "assets/meme/cat_ears.png",
        "placement": "top",
        "scale": 1.6,
        "y_offset_ratio": -0.6,
    },
    {
        "key": "meme",
        "path": "assets/meme/meme.png",
        "placement": "face",
        "scale": 1.5,
        "y_offset_ratio": 0.0,
    },
]


class MemeManager:
    """
    Sticker Mode (formerly Meme Mode).
    - `toggle_mode()` → aktif/nonaktif sticker overlay
    - `cycle_sticker()` → ganti ke sticker berikutnya
    - `apply_meme(frame)` → tempel sticker ke frame (opencv BGR)
    """

    def __init__(self):
        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self.eye_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_eye.xml"
        )

        # Load semua sticker
        self._sticker_images = {}
        for s in STICKERS:
            img = self._try_load(s["path"])
            if img is None:
                # Fallback: coba tanpa prefix path
                fallback = os.path.join("assets", "meme", os.path.basename(s["path"]))
                img = self._try_load(fallback)
            self._sticker_images[s["key"]] = img

        self.meme_mode = False
        self.sticker_idx = 0  # index ke STICKERS

    @staticmethod
    def _try_load(path):
        if os.path.exists(path):
            try:
                return Image.open(path).convert("RGBA")
            except Exception:
                pass
        return None

    # ── Public API ────────────────────────────────────────────────

    def toggle_mode(self):
        self.meme_mode = not self.meme_mode
        name = STICKERS[self.sticker_idx]["key"] if self.meme_mode else "OFF"
        print(f"Sticker Mode: {name}")

    def cycle_sticker(self):
        self.sticker_idx = (self.sticker_idx + 1) % len(STICKERS)
        print(f"Sticker: {STICKERS[self.sticker_idx]['key']}")

    def apply_meme(self, frame):
        """Tempel sticker aktif ke frame (BGR numpy array)."""
        if not self.meme_mode:
            return frame

        sticker_def = STICKERS[self.sticker_idx]
        sticker_img = self._sticker_images.get(sticker_def["key"])
        if sticker_img is None:
            return frame

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)

        frame_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        if len(faces) > 0:
            for (fx, fy, fw, fh) in faces:
                placement = sticker_def["placement"]
                scale = sticker_def["scale"]
                y_off_ratio = sticker_def["y_offset_ratio"]

                if placement == "eyes":
                    # Posisikan di area mata (atas wajah)
                    roi_gray = gray[fy : fy + fh // 2, fx : fx + fw]
                    eyes = self.eye_cascade.detectMultiScale(roi_gray)
                    if len(eyes) >= 2:
                        # Rata-rata posisi dua mata
                        ex = int(np.mean([e[0] for e in eyes]))
                        ey = int(np.mean([e[1] for e in eyes]))
                        paste_x, paste_y = fx + ex - fw // 3, fy + ey - fh // 10
                    else:
                        paste_x = fx - int(fw * 0.15)
                        paste_y = fy + int(fh * 0.1)
                    stk_w = int(fw * scale)
                    stk_h = int(stk_w * sticker_img.height / sticker_img.width)
                elif placement == "top":
                    stk_w = int(fw * scale)
                    stk_h = int(stk_w * sticker_img.height / sticker_img.width)
                    paste_x = fx + fw // 2 - stk_w // 2
                    paste_y = fy + int(fh * y_off_ratio) - stk_h // 2
                elif placement == "mouth":
                    stk_w = int(fw * scale)
                    stk_h = int(stk_w * sticker_img.height / sticker_img.width)
                    paste_x = fx + fw // 2 - stk_w // 2
                    paste_y = fy + int(fh * (0.65 + y_off_ratio))
                else:  # face
                    stk_w = int(fw * scale)
                    stk_h = int(stk_w * sticker_img.height / sticker_img.width)
                    paste_x = fx + fw // 2 - stk_w // 2
                    paste_y = fy + fh // 2 - stk_h // 2

                sticker_resized = sticker_img.resize((stk_w, stk_h), Image.LANCZOS)
                # Clip paste coords
                px = max(0, paste_x)
                py = max(0, paste_y)
                frame_pil.paste(sticker_resized, (px, py), sticker_resized)
        else:
            # Tidak ada wajah → tampilkan di tengah layar
            fh_frame, fw_frame = frame.shape[:2]
            stk_w = 220
            stk_h = int(stk_w * sticker_img.height / sticker_img.width)
            sticker_resized = sticker_img.resize((stk_w, stk_h), Image.LANCZOS)
            px = (fw_frame - stk_w) // 2
            py = (fh_frame - stk_h) // 2
            frame_pil.paste(sticker_resized, (px, py), sticker_resized)

        return cv2.cvtColor(np.array(frame_pil), cv2.COLOR_RGB2BGR)
