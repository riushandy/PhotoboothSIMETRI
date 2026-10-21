from PIL import Image, ImageDraw, ImageFont
import os

def create_aesthetic_overlay():
    width = 600
    height = 1800
    
    # Warna background: Putih sedikit warm (Vintage Paper)
    bg_color = (250, 245, 240, 255)
    text_color = (40, 40, 40, 255)
    
    # Buat base image
    base = Image.new('RGBA', (width, height), bg_color)
    draw = ImageDraw.Draw(base)
    
    # Konfigurasi lubang foto
    photo_width = 500
    photo_height = 375
    padding_y = 40
    
    # Header space
    header_height = 150
    start_y = header_height
    start_x = (width - photo_width) // 2
    
    # 1. Buat Lubang Transparan
    # Kita gunakan mask 'L' mode. 0 = transparan, 255 = opaque
    mask = Image.new('L', (width, height), 255)
    draw_mask = ImageDraw.Draw(mask)
    
    for i in range(4):
        y = start_y + (i * (photo_height + padding_y))
        # Gambar kotak hitam di mask (lubang)
        draw_mask.rectangle([start_x, y, start_x + photo_width, y + photo_height], fill=0)
        
        # Gambar border di base image (bukan di mask)
        draw.rectangle([start_x, y, start_x + photo_width, y + photo_height], outline=text_color, width=3)
    
    # Terapkan mask ke alpha channel base
    base.putalpha(mask)
    
    # 2. Tambah Teks (Header & Footer)
    # Kita perlu draw ulang setelah putalpha karena putalpha me-replace alpha channel
    # Tapi draw object lama masih refer ke image yang sama, jadi aman.
    
    # Coba load font keren jika ada, kalau tidak pakai default
    try:
        # Coba cari font sistem umum
        font_large = ImageFont.truetype("arial.ttf", 60)
        font_small = ImageFont.truetype("arial.ttf", 30)
    except IOError:
        font_large = None
        font_small = None

    # Helper untuk center text
    def draw_centered_text(text, y, font, fill):
        if font:
            # Gunakan getbbox untuk ukuran teks
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            x = (width - text_w) // 2
            draw.text((x, y), text, font=font, fill=fill)
        else:
            # Fallback default font (kecil)
            draw.text((width//2 - 30, y), text, fill=fill)

    draw_centered_text("PHOTO BOOTH", 50, font_large, text_color)
    
    # Footer date
    from datetime import datetime
    date_str = datetime.now().strftime("%d %B %Y")
    draw_centered_text(date_str, height - 100, font_small, text_color)
    
    # Simpan
    base.save("overlay.png")
    print("Overlay aesthetic berhasil dibuat: overlay.png")

if __name__ == "__main__":
    create_aesthetic_overlay()
