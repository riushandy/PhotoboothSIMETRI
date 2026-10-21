import cv2
import numpy as np

FILTER_NORMAL = 0
FILTER_GRAYSCALE = 1
FILTER_VINTAGE = 2
FILTER_BEAUTY = 3
FILTER_VIVID = 4

FILTER_NAMES = ["NORMAL", "GRAYSCALE", "VINTAGE", "BEAUTY", "VIVID"]
FILTER_COUNT = len(FILTER_NAMES)


def apply_grayscale(frame):
    """Hitam putih klasik."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def apply_vintage(frame):
    """Warm tone sepia + subtle vignette."""
    # Sepia matrix
    sepia_kernel = np.array([
        [0.272, 0.534, 0.131],
        [0.349, 0.686, 0.168],
        [0.393, 0.769, 0.189],
    ])
    sepia = cv2.transform(frame, sepia_kernel)
    sepia = np.clip(sepia, 0, 255).astype(np.uint8)

    # Vignette
    h, w = sepia.shape[:2]
    Y, X = np.ogrid[:h, :w]
    cx, cy = w / 2, h / 2
    dist = np.sqrt(((X - cx) / (w * 0.6)) ** 2 + ((Y - cy) / (h * 0.6)) ** 2)
    vignette = np.clip(1 - dist, 0, 1)
    vignette = vignette[:, :, np.newaxis]
    result = (sepia * vignette).astype(np.uint8)
    return result


def apply_beauty(frame):
    """Bilateral filter untuk skin smoothing."""
    smooth = cv2.bilateralFilter(frame, d=9, sigmaColor=75, sigmaSpace=75)
    # Slight brightness boost
    smooth = cv2.convertScaleAbs(smooth, alpha=1.05, beta=8)
    return smooth


def apply_vivid(frame):
    """Saturation boost + contrast tinggi."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.5, 0, 255)   # Saturation ×1.5
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * 1.1, 0, 255)   # Value ×1.1
    vivid = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    # Contrast via CLAHE on L channel
    lab = cv2.cvtColor(vivid, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    vivid = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
    return vivid


def apply_filter(frame, filter_idx):
    """Dispatch ke filter berdasarkan index."""
    if filter_idx == FILTER_GRAYSCALE:
        return apply_grayscale(frame)
    elif filter_idx == FILTER_VINTAGE:
        return apply_vintage(frame)
    elif filter_idx == FILTER_BEAUTY:
        return apply_beauty(frame)
    elif filter_idx == FILTER_VIVID:
        return apply_vivid(frame)
    return frame  # NORMAL


def get_filter_name(idx):
    if 0 <= idx < FILTER_COUNT:
        return FILTER_NAMES[idx]
    return "NORMAL"
