from PIL import Image, ImageDraw

def create_thug_glasses():
    # Ukuran kacamata
    w, h = 300, 50
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Warna hitam
    black = (0, 0, 0, 255)
    white = (255, 255, 255, 255)
    
    # Gambar kotak kacamata (pixel art style simple)
    # Kiri
    draw.rectangle([0, 0, 130, 50], fill=black)
    # Kanan
    draw.rectangle([170, 0, 300, 50], fill=black)
    # Jembatan
    draw.rectangle([130, 10, 170, 20], fill=black)
    
    # Pixel putih (kilau)
    # Kiri
    draw.rectangle([10, 10, 30, 20], fill=white)
    draw.rectangle([20, 20, 40, 30], fill=white)
    draw.rectangle([30, 30, 50, 40], fill=white)
    
    # Kanan
    draw.rectangle([180, 10, 200, 20], fill=white)
    draw.rectangle([190, 20, 210, 30], fill=white)
    draw.rectangle([200, 30, 220, 40], fill=white)
    
    img.save("assets/meme/glasses.png")
    print("Glasses created.")

if __name__ == "__main__":
    create_thug_glasses()
