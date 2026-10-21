import cv2
import time
import os
import uuid
from datetime import datetime
from PIL import Image

# Konfigurasi
OVERLAY_PATH = "overlay.png"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "riwayat")
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_FILENAME = os.path.join(
    OUTPUT_DIR,
    f"hasil_photobooth_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}.jpg",
)
PHOTO_COUNT = 4
DELAY_BETWEEN_PHOTOS = 3 # detik

# Konfigurasi Layout (Harus cocok dengan overlay.png)
CANVAS_WIDTH = 600
CANVAS_HEIGHT = 1800
PHOTO_WIDTH = 500
PHOTO_HEIGHT = 375
# Update posisi sesuai create_overlay.py yang baru
# Header 150, Padding 40
START_Y = 150 
PADDING_Y = 40
START_X = (CANVAS_WIDTH - PHOTO_WIDTH) // 2

def generate_overlay_if_missing():
    if not os.path.exists(OVERLAY_PATH):
        print(f"File {OVERLAY_PATH} tidak ditemukan. Membuat sample overlay...")
        try:
            import create_overlay
            create_overlay.create_aesthetic_overlay()
        except ImportError:
            print("Gagal membuat overlay otomatis. Pastikan create_overlay.py ada.")
        except AttributeError:
             # Fallback jika create_aesthetic_overlay belum ada di module yang ter-cache
             import create_overlay
             create_overlay.create_sample_overlay()

def draw_ui(frame, text, color=(255, 255, 255), bg_color=(0, 0, 0)):
    # Tambahkan background semi-transparan untuk teks
    h, w, _ = frame.shape
    
    # Hitung ukuran teks
    font_scale = 2
    thickness = 3
    (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
    
    x = (w - text_w) // 2
    y = (h + text_h) // 2
    
    # Gambar kotak background
    padding = 20
    cv2.rectangle(frame, (x - padding, y - text_h - padding), (x + text_w + padding, y + padding), bg_color, -1)
    
    # Gambar teks
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, thickness)

def capture_photos():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Tidak dapat mengakses kamera.")
        return []

    # Set resolusi kamera ke yang lebih tinggi jika didukung
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    photos = []
    print("Tekan SPASI untuk memulai sesi foto...")

    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        # Flip horizontal biar seperti cermin
        frame = cv2.flip(frame, 1)
        
        display_frame = frame.copy()
        
        # Gambar guide rectangle (area yang akan di-crop)
        # Asumsi rasio 4:3 (500x375)
        h, w, _ = display_frame.shape
        
        # Hitung crop box di tengah layar
        target_ratio = PHOTO_WIDTH / PHOTO_HEIGHT
        crop_h = int(h * 0.8)
        crop_w = int(crop_h * target_ratio)
        
        cx, cy = w // 2, h // 2
        x1 = cx - crop_w // 2
        y1 = cy - crop_h // 2
        x2 = cx + crop_w // 2
        y2 = cy + crop_h // 2
        
        # Gambar kotak guide
        cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 255, 255), 2)
        cv2.putText(display_frame, "Area Foto", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        # Instruksi
        cv2.putText(display_frame, "Tekan SPASI untuk Mulai", (50, h - 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        cv2.imshow("Photo Booth", display_frame)
        
        key = cv2.waitKey(1)
        if key % 256 == 32: # Space bar
            break
        elif key % 256 == 27: # ESC
            cap.release()
            cv2.destroyAllWindows()
            return []

    # Mulai sesi foto
    for i in range(PHOTO_COUNT):
        # Countdown / Jeda
        start_time = time.time()
        while time.time() - start_time < DELAY_BETWEEN_PHOTOS:
            ret, frame = cap.read()
            if not ret:
                break
            
            frame = cv2.flip(frame, 1)
            display_frame = frame.copy()
            
            # Gambar guide lagi
            # Hitung ulang koordinat karena bisa jadi frame berubah (jarang sih)
            h, w, _ = display_frame.shape
            target_ratio = PHOTO_WIDTH / PHOTO_HEIGHT
            crop_h = int(h * 0.8)
            crop_w = int(crop_h * target_ratio)
            cx, cy = w // 2, h // 2
            x1 = cx - crop_w // 2
            y1 = cy - crop_h // 2
            x2 = cx + crop_w // 2
            y2 = cy + crop_h // 2
            
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 255, 255), 2)
            
            # Tampilkan countdown di tengah
            remaining = int(DELAY_BETWEEN_PHOTOS - (time.time() - start_time)) + 1
            draw_ui(display_frame, str(remaining))
            
            cv2.imshow("Photo Booth", display_frame)
            cv2.waitKey(1)

        # Ambil foto
        ret, frame = cap.read()
        if ret:
            frame = cv2.flip(frame, 1)
            
            # Crop sesuai guide
            # Kita simpan full frame dulu, nanti di process_photos kita crop center
            # Tapi biar preview bagus, kita crop sekarang? 
            # Lebih aman simpan full frame, crop nanti.
            photos.append(frame)
            print(f"Foto {i+1} tersimpan!")
            
            # Flash effect (putih sebentar)
            white_frame = 255 * (frame * 0 + 1) # frame putih
            cv2.imshow("Photo Booth", white_frame) 
            cv2.waitKey(100)
            
            # Tampilkan hasil sebentar (freeze frame)
            cv2.imshow("Photo Booth", frame)
            cv2.waitKey(500)
        else:
            print("Gagal mengambil frame.")

    cap.release()
    cv2.destroyAllWindows()
    return photos

def process_photos(photos):
    if len(photos) != PHOTO_COUNT:
        print("Jumlah foto tidak cukup.")
        return

    print("Memproses gambar...")
    
    # 1. Buat Canvas Putih
    final_image = Image.new('RGB', (CANVAS_WIDTH, CANVAS_HEIGHT), (255, 255, 255))
    
    # 2. Tempel Foto-foto
    for i, photo_cv in enumerate(photos):
        photo_rgb = cv2.cvtColor(photo_cv, cv2.COLOR_BGR2RGB)
        photo_pil = Image.fromarray(photo_rgb)
        
        # Crop Center agar rasio pas 4:3
        target_ratio = PHOTO_WIDTH / PHOTO_HEIGHT
        img_ratio = photo_pil.width / photo_pil.height
        
        if img_ratio > target_ratio:
            new_width = int(photo_pil.height * target_ratio)
            offset = (photo_pil.width - new_width) // 2
            photo_pil = photo_pil.crop((offset, 0, offset + new_width, photo_pil.height))
        else:
            new_height = int(photo_pil.width / target_ratio)
            offset = (photo_pil.height - new_height) // 2
            photo_pil = photo_pil.crop((0, offset, photo_pil.width, offset + new_height))
            
        photo_pil = photo_pil.resize((PHOTO_WIDTH, PHOTO_HEIGHT), Image.LANCZOS)
        
        # Hitung posisi Y
        y = START_Y + (i * (PHOTO_HEIGHT + PADDING_Y))
        
        # Tempel ke canvas
        final_image.paste(photo_pil, (START_X, y))
        
    # 3. Tempel Overlay
    if os.path.exists(OVERLAY_PATH):
        overlay = Image.open(OVERLAY_PATH).convert("RGBA")
        if overlay.size != (CANVAS_WIDTH, CANVAS_HEIGHT):
             overlay = overlay.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.LANCZOS)
             
        # Tempel overlay dengan alpha composite
        final_image.paste(overlay, (0, 0), overlay)
    else:
        print("Warning: Overlay tidak ditemukan.")

    # 4. Simpan
    final_image.save(OUTPUT_FILENAME)
    final_image.show()
    print(f"Selesai! Disimpan di {OUTPUT_FILENAME}")

if __name__ == "__main__":
    generate_overlay_if_missing()
    captured_photos = capture_photos()
    if captured_photos:
        process_photos(captured_photos)
