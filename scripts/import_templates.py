import cv2
import numpy as np
import os
import json
from PIL import Image

TEMPLATES_DIR = "assets/templates"

def find_transparent_holes(image_path):
    # Load image
    img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None, None, []
    
    h, w = img.shape[:2]
    
    # Helper to sort holes
    def sort_holes(holes):
        return sorted(holes, key=lambda b: (b[1] // 50, b[0]))

    # Minimum size threshold (e.g. 15% of the smallest dimension)
    min_dim = min(w, h)
    min_size = min_dim * 0.15
    
    print(f"  Debug: Image size {w}x{h}. Min hole size set to {int(min_size)}px.")

    # Case 1: Image has Alpha Channel (4 channels)
    if len(img.shape) == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        if np.min(alpha) == 255:
            print("  -> Alpha channel detected but it is fully opaque. Treating as BGR.")
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        else:
            _, binary = cv2.threshold(alpha, 10, 255, cv2.THRESH_BINARY_INV)
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            holes = []
            for cnt in contours:
                x, y, cw, ch = cv2.boundingRect(cnt)
                # Use dynamic threshold
                if cw > min_size and ch > min_size:
                    holes.append((x, y, cw, ch))
            
            if not holes:
                print("  -> Alpha channel ada, tapi tidak ada lubang yang cukup besar. Mengabaikan alpha.")
                # Fallthrough to detection logic (maybe the alpha was just noise)
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            else:
                print(f"  -> Menggunakan Alpha Channel. Ditemukan {len(holes)} lubang valid.")
                return w, h, sort_holes(holes)

    # Case 2: Image is Grayscale or BGR
    if len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif len(img.shape) == 3 and img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        
    print(f"Warning: {image_path} tidak punya transparansi valid. Mencoba mendeteksi kotak Putih/Hitam.")
    
    gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    
    # Detect White Rectangles
    _, binary_white = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY)
    
    # Detect Black Rectangles
    _, binary_black = cv2.threshold(gray, 5, 255, cv2.THRESH_BINARY_INV)
    
    def get_contours_and_holes(binary_map):
        contours, _ = cv2.findContours(binary_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_contours = []
        valid_holes = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            # Filter noise: Must be reasonably large
            if cw > min_size and ch > min_size:
                if cw > w * 0.95 and ch > h * 0.95:
                    continue 
                valid_contours.append(cnt)
                valid_holes.append((x, y, cw, ch))
        return valid_contours, valid_holes

    contours_white, holes_white = get_contours_and_holes(binary_white)
    contours_black, holes_black = get_contours_and_holes(binary_black)
    
    final_contours = []
    final_holes = []
    
    # Heuristic: Choose the one that finds 1-8 holes
    if 0 < len(holes_white) <= 8:
        print("  -> Mendeteksi area PUTIH sebagai lubang.")
        final_contours = contours_white
        final_holes = holes_white
    elif 0 < len(holes_black) <= 8:
        print("  -> Mendeteksi area HITAM sebagai lubang.")
        final_contours = contours_black
        final_holes = holes_black
    else:
        print("  -> Gagal mendeteksi lubang otomatis.")
        return w, h, []
        
    # Apply transparency using contours to preserve shapes (rounded corners)
    # Create a mask for the holes
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.drawContours(mask, final_contours, -1, 255, thickness=cv2.FILLED)
    
    # Set alpha to 0 where mask is 255
    img[mask == 255, 3] = 0
        
    # Overwrite the file so it becomes a proper transparent PNG
    cv2.imwrite(image_path, img)
    print("  -> File overlay.png telah diupdate dengan transparansi (mengikuti bentuk lubang).")
    
    return w, h, sort_holes(final_holes)

def generate_config_and_thumb(folder_name):
    folder_path = os.path.join(TEMPLATES_DIR, folder_name)
    overlay_path = os.path.join(folder_path, "overlay.png")
    config_path = os.path.join(folder_path, "config.json")
    thumb_path = os.path.join(folder_path, "thumbnail.png")
    
    if not os.path.exists(overlay_path):
        return
        
    print(f"Processing {folder_name}...")
    
    # Always try to detect holes if config is missing OR if we want to force update
    # Force update config if it exists to fix bad detection
    if True: # Always run detection
        w, h, holes = find_transparent_holes(overlay_path)
        if holes:
            config = {
                "name": folder_name.replace("_", " ").title(),
                "width": w,
                "height": h,
                "photo_count": len(holes),
                "layout": holes, # [x, y, w, h]
                "overlay_path": "overlay.png"
            }
            with open(config_path, "w") as f:
                json.dump(config, f, indent=4)
            print(f"  - Config generated/updated with {len(holes)} photos.")
        else:
            print("  - Gagal mendeteksi lubang.")
            
    # Generate Thumbnail
    if not os.path.exists(thumb_path):
        try:
            img = Image.open(overlay_path)
            img.thumbnail((200, 200))
            img.save(thumb_path)
            print("  - Thumbnail generated.")
        except Exception as e:
            print(f"  - Gagal membuat thumbnail: {e}")

def scan_and_fix():
    if not os.path.exists(TEMPLATES_DIR):
        os.makedirs(TEMPLATES_DIR)
        
    for folder in os.listdir(TEMPLATES_DIR):
        path = os.path.join(TEMPLATES_DIR, folder)
        if os.path.isdir(path):
            generate_config_and_thumb(folder)

if __name__ == "__main__":
    scan_and_fix()
