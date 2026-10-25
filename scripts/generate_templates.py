import json
import os

from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = "assets/templates"
os.makedirs(ASSETS_DIR, exist_ok=True)

def create_template_files(name, width, height, photos_layout, bg_color, text_color, title):
    """
    photos_layout: list of (x, y, w, h)
    """
    # 1. Create Overlay Image (Transparent holes)
    base = Image.new('RGBA', (width, height), bg_color)
    draw = ImageDraw.Draw(base)
    
    # Create Mask
    mask = Image.new('L', (width, height), 255)
    draw_mask = ImageDraw.Draw(mask)
    
    for (x, y, w, h) in photos_layout:
        draw_mask.rectangle([x, y, x+w, y+h], fill=0)
        # Border
        draw.rectangle([x, y, x+w, y+h], outline=text_color, width=5)
        
    base.putalpha(mask)
    
    # Add Text
    try:
        font = ImageFont.truetype("arial.ttf", 60)
    except:
        font = None
        
    bbox = draw.textbbox((0, 0), title, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(((width - text_w)//2, 50), title, fill=text_color, font=font)
    
    # Save Overlay
    safe_name = name.lower().replace(" ", "_")
    folder = os.path.join(ASSETS_DIR, safe_name)
    os.makedirs(folder, exist_ok=True)
    
    overlay_path = os.path.join(folder, "overlay.png")
    base.save(overlay_path)
    
    # 2. Create Thumbnail (Preview)
    # Fill holes with gray for preview
    preview = base.copy()
    preview_draw = ImageDraw.Draw(preview)
    for (x, y, w, h) in photos_layout:
        preview_draw.rectangle([x, y, x+w, y+h], fill=(200, 200, 200, 255))
        # Add number
        preview_draw.text((x + w//2, y + h//2), "PHOTO", fill=(100,100,100,255))
        
    thumb_path = os.path.join(folder, "thumbnail.png")
    # Resize for UI
    preview.thumbnail((200, 200))
    preview.save(thumb_path)
    
    # 3. Save Config
    config = {
        "name": name,
        "width": width,
        "height": height,
        "photo_count": len(photos_layout),
        "layout": photos_layout, # list of [x, y, w, h]
        "overlay_path": "overlay.png"
    }
    
    with open(os.path.join(folder, "config.json"), "w") as f:
        json.dump(config, f, indent=4)
        
    print(f"Template '{name}' created in {folder}")

def generate_all():
    # 1. Classic Strip (4 Vertical)
    # 600 x 1800
    # Photo: 500 x 375
    layout_strip = []
    start_y = 150
    for i in range(4):
        layout_strip.append((50, start_y + i*(375+40), 500, 375))
        
    create_template_files(
        "Classic Strip", 
        600, 1800, 
        layout_strip, 
        (255, 248, 220, 255), # Cream
        (50, 50, 50, 255),
        "CLASSIC STRIP"
    )
    
    # 2. Modern Grid (2x2)
    # 1200 x 1600
    # Photo: 500 x 500 (Square)
    layout_grid = []
    # Row 1
    layout_grid.append((75, 200, 500, 500))
    layout_grid.append((625, 200, 500, 500))
    # Row 2
    layout_grid.append((75, 750, 500, 500))
    layout_grid.append((625, 750, 500, 500))
    
    create_template_files(
        "Modern Grid",
        1200, 1600,
        layout_grid,
        (20, 20, 20, 255), # Dark
        (255, 255, 255, 255),
        "MODERN GRID"
    )
    
    # 3. Wide Trio (3 Horizontal-ish)
    # 1800 x 1200
    # Photo: 500 x 375
    layout_wide = []
    # 3 photos side by side? No, maybe 1 big 2 small?
    # Let's do 3 photos in a row
    # Total width 1800. 3 photos of 500 = 1500. Spacing 300/4 = 75
    y = 300
    layout_wide.append((75, y, 500, 375))
    layout_wide.append((650, y, 500, 375))
    layout_wide.append((1225, y, 500, 375))
    
    create_template_files(
        "Wide Trio",
        1800, 900,
        layout_wide,
        (255, 200, 200, 255), # Pink
        (100, 0, 0, 255),
        "PINK TRIO"
    )

if __name__ == "__main__":
    generate_all()
