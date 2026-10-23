"""Server tablet photobooth: laptop = kamera + logika, tablet = layar sentuh (browser).

Jalankan:  python tablet_server.py            (kamera asli)
           python tablet_server.py --fake     (tanpa kamera, tes koneksi)
"""
import argparse, io, json, math, os, shutil, socket, subprocess, threading, time
import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_file, send_from_directory, abort
from PIL import Image
from filter_engine import apply_filter, get_filter_name, FILTER_COUNT

ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(ROOT, "assets", "templates")
UI_DIR = os.path.join(ROOT, "tablet_ui")
OUT_DIR = os.path.join(ROOT, "riwayat")
COUNTDOWN = 10        # detik per foto
VIDEO_SECONDS = 5     # video = N detik terakhir sebelum jepret
VIDEO_FPS = 30
OUTPUT_SCALE = 4         # foto hasil = ukuran template x 4
VIDEO_MAX_SIZE = (1440, 2160)  # batas ukuran video (lebar, tinggi) agar mulus di tablet
VIDEO_CLIP_WIDTH = 1280  # lebar klip sumber video

app = Flask(__name__)
state_lock = threading.Lock()
cancel_flag = threading.Event()
state = {"phase": "idle", "count": 0, "shot": 0, "total": 0, "template": None, "stamp": 0,
         "slots": [], "canvas": [1, 1], "has_video": False, "filter": 0,
         "upload": "", "link": None, "print": "", "error": "", "error_id": 0}
S = {"dir": None, "photos": [], "clips": [], "cfg": None}


def fail(msg):
    state.update(error=msg, error_id=state["error_id"] + 1)


# ── Kamera: satu pemilik, frame dibagikan ke preview & jepret ───────────
class Camera:
    def __init__(self, index, fake):
        self.index, self.fake, self.frame = index, fake, None
        self.lock, self.reopen = threading.Lock(), threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def switch(self, index):
        self.index = index
        self.reopen.set()

    def _open(self):
        if self.fake:
            return None
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW if os.name == "nt" else 0)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        if not cap.isOpened():
            print("! Kamera tidak bisa dibuka, pakai frame palsu.")
            return None
        print("Kamera:", int(cap.get(3)), "x", int(cap.get(4)))
        return cap

    def _loop(self):
        cap, t = self._open(), 0
        while True:
            if self.reopen.is_set():
                self.reopen.clear()
                if cap is not None:
                    cap.release()
                cap = self._open()
            if cap is not None:
                ok, f = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
            else:
                t += 1
                f = np.zeros((720, 1280, 3), np.uint8)
                f[:] = (40 + t % 120, 90, 160)
                cv2.putText(f, time.strftime("%H:%M:%S"), (380, 400), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
                time.sleep(1 / 20)
            with self.lock:
                self.frame = f

    def get(self, filtered=True):
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        return apply_filter(f, state["filter"]) if (f is not None and filtered) else f


def probe_cameras(fake):
    if fake:
        return ["Kamera palsu"]
    found = []
    for i in range(4):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if os.name == "nt" else 0)
        if cap.isOpened():
            found.append(f"Kamera {i}")
        cap.release()
    return found or ["Kamera 0"]


# ── Template, komposit, video ───────────────────────────────────────────
def list_templates():
    items = []
    for d in sorted(os.listdir(TEMPLATES)):
        cfg = os.path.join(TEMPLATES, d, "config.json")
        if os.path.exists(cfg) and os.path.exists(os.path.join(TEMPLATES, d, "overlay.png")):
            c = json.load(open(cfg, encoding="utf-8"))
            items.append({"id": d, "name": c.get("name", d), "count": c["photo_count"]})
    return items


def cover(img, w, h):
    r, (iw, ih) = w / h, img.size
    if iw / ih > r:
        nw = int(ih * r); l = (iw - nw) // 2
        img = img.crop((l, 0, l + nw, ih))
    else:
        nh = int(iw / r); t = (ih - nh) // 2
        img = img.crop((0, t, iw, t + nh))
    return img.resize((w, h), Image.LANCZOS)


def compose(photos, tid):
    tdir = os.path.join(TEMPLATES, tid)
    cfg = json.load(open(os.path.join(tdir, "config.json"), encoding="utf-8"))
    W, H, k = cfg["width"], cfg["height"], OUTPUT_SCALE
    canvas = Image.new("RGB", (W * k, H * k), "white")
    for img, (x, y, w, h) in zip(photos, cfg["layout"]):
        canvas.paste(cover(img, w * k, h * k), (x * k, y * k))
    ov = Image.open(os.path.join(tdir, "overlay.png")).convert("RGBA")
    if ov.size != canvas.size:
        ov = ov.resize(canvas.size, Image.LANCZOS)
    canvas.paste(ov, (0, 0), ov)
    return canvas, cfg


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # biner ffmpeg bawaan paket pip
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def build_video(clips, tid, path):
    """Video = strip lengkap dengan frame; tiap slot memutar VIDEO_SECONDS detik terakhir
    sebelum jepretannya, ketiganya bersamaan. Frame dikirim langsung ke ffmpeg (H.264,
    CRF 18) tanpa kompresi perantara supaya tetap tajam."""
    if not all(clips):
        return False
    tdir = os.path.join(TEMPLATES, tid)
    cfg = json.load(open(os.path.join(tdir, "config.json"), encoding="utf-8"))
    k = OUTPUT_SCALE
    W, H = cfg["width"] * k, cfg["height"] * k
    sc = min(1, VIDEO_MAX_SIZE[0] / W, VIDEO_MAX_SIZE[1] / H)
    ow, oh = round(W * sc) // 2 * 2, round(H * sc) // 2 * 2
    ov = Image.open(os.path.join(tdir, "overlay.png")).convert("RGBA").resize((ow, oh), Image.LANCZOS)
    slots = [(round(x * k * sc), round(y * k * sc), max(1, round(w * k * sc)), max(1, round(h * k * sc)))
             for x, y, w, h in cfg["layout"]]

    ff, proc, vw, tmp = ffmpeg_exe(), None, None, path + ".tmp.mp4"
    if ff:
        proc = subprocess.Popen([ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                 "-s", f"{ow}x{oh}", "-r", str(VIDEO_FPS), "-i", "-", "-c:v", "libx264",
                                 "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
                                 "-movflags", "+faststart", path], stdin=subprocess.PIPE)
    else:
        print("! ffmpeg tidak ada (pip install imageio-ffmpeg): video mp4v, kualitas lebih rendah.")
        vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), float(VIDEO_FPS), (ow, oh))

    ptr, cache = [0] * len(slots), {}
    for n in range(int(VIDEO_SECONDS * VIDEO_FPS)):
        canvas = Image.new("RGB", (ow, oh), "white")
        for si, (x, y, w, h) in enumerate(slots):
            clip = clips[si]
            t = clip[-1][0] - VIDEO_SECONDS + n / VIDEO_FPS
            while ptr[si] + 1 < len(clip) and clip[ptr[si] + 1][0] <= t:
                ptr[si] += 1
            if si not in cache or cache[si][0] != ptr[si]:
                bgr = cv2.imdecode(np.frombuffer(clip[ptr[si]][1], np.uint8), cv2.IMREAD_COLOR)
                cache[si] = (ptr[si], Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
            canvas.paste(cover(cache[si][1], w, h), (x, y))
        canvas.paste(ov, (0, 0), ov)
        if proc:
            proc.stdin.write(canvas.tobytes())
        else:
            vw.write(cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR))
    if proc:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg gagal membuat video")
    else:
        vw.release()
        os.replace(tmp, path)
    return True


# ── Sesi ────────────────────────────────────────────────────────────────
def shoot(i, total):
    clip, end = [], time.monotonic() + COUNTDOWN
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        if cancel_flag.is_set():
            return None
        state.update(phase="countdown", count=math.ceil(left), shot=i + 1, total=total)
        if left <= VIDEO_SECONDS + 0.5:
            f = cam.get()
            f = cv2.resize(f, (VIDEO_CLIP_WIDTH, int(VIDEO_CLIP_WIDTH * f.shape[0] / f.shape[1])))
            ok, buf = cv2.imencode(".jpg", cv2.flip(f, 1), [cv2.IMWRITE_JPEG_QUALITY, 92])  # mirror + hemat memori
            clip.append((time.monotonic(), buf.tobytes()))
        time.sleep(1 / VIDEO_FPS)
    photo = Image.fromarray(cv2.cvtColor(cv2.flip(cam.get(), 1), cv2.COLOR_BGR2RGB))
    state.update(phase="flash", count=0)
    time.sleep(0.4)
    return photo, clip


def run_session(tid, indices, total):
    try:
        for i in indices:
            got = shoot(i, total)
            if got is None:
                state.update(phase="idle")
                return
            S["photos"][i], S["clips"][i] = got
        state.update(phase="processing")
        d = S["dir"]
        for i, p in enumerate(S["photos"]):
            p.save(os.path.join(d, f"foto_raw_{i + 1}.jpg"), quality=97, subsampling=0)
        img, cfg = compose(S["photos"], tid)
        dpi = 300 * OUTPUT_SCALE  # ukuran cetak fisik tetap sama
        img.save(os.path.join(d, "foto_frame.jpg"), quality=95, subsampling=0, dpi=(dpi, dpi))
        prev = img.copy()
        prev.thumbnail((1000, 1600))
        prev.save(os.path.join(d, ".preview.jpg"), quality=88)  # untuk layar tablet (tidak ikut upload)
        has_video = build_video(S["clips"], tid, os.path.join(d, "video.mp4"))
        W, H = cfg["width"], cfg["height"]
        slots = [{"l": x / W * 100, "t": y / H * 100, "w": w / W * 100, "h": h / H * 100} for x, y, w, h in cfg["layout"]]
        state.update(phase="result", stamp=time.time(), slots=slots, canvas=[W, H],
                     has_video=has_video, upload="", link=None)
    except Exception as e:
        state.update(phase="idle")
        fail(f"Sesi gagal: {e}")


def begin(tid, only=None):
    tpl = next((t for t in list_templates() if t["id"] == tid), None)
    if tpl is None:
        return False, "template tidak ada"
    with state_lock:
        if state["phase"] not in ("idle", "result"):
            return False, "sesi sedang berjalan"
        cancel_flag.clear()
        n = tpl["count"]
        if only is None:  # sesi baru
            os.makedirs(OUT_DIR, exist_ok=True)
            S.update(dir=os.path.join(OUT_DIR, time.strftime("tablet_%Y%m%d_%H%M%S")), photos=[None] * n, clips=[None] * n)
            os.makedirs(S["dir"], exist_ok=True)
        state.update(phase="countdown", template=tid, total=n, upload="", link=None, error="")
    threading.Thread(target=run_session, args=(tid, [only] if only is not None else range(n), n), daemon=True).start()
    return True, ""


def do_upload():
    try:
        from upload_manager import UploadManager
        d = S["dir"]
        files = sorted(os.path.join(d, f) for f in os.listdir(d) if not f.startswith(".") and f.endswith((".jpg", ".mp4")))
        link = UploadManager().upload_session(files, os.path.basename(d))
        if not link:
            raise RuntimeError("tidak ada link")
        state.update(link=link, upload="done")
    except Exception as e:
        state.update(upload="")
        fail(f"Upload gagal: {e}")


def do_print():
    try:
        from print_manager import PrintManager
        ok = PrintManager.print_image(os.path.join(S["dir"], "foto_frame.jpg"))
        state.update(print="done" if ok else "error")
    except Exception:
        state.update(print="error")


# ── Route ───────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.get("/api/templates")
def api_templates():
    return jsonify(list_templates())


@app.get("/thumb/<tid>")
def thumb(tid):
    if tid not in [t["id"] for t in list_templates()]:
        abort(404)
    d = os.path.join(TEMPLATES, tid)
    return send_from_directory(d, "thumbnail.png" if os.path.exists(os.path.join(d, "thumbnail.png")) else "overlay.png")


@app.get("/api/status")
def api_status():
    return jsonify(state)


@app.get("/api/filters")
def api_filters():
    return jsonify(names=[get_filter_name(i) for i in range(FILTER_COUNT)], current=state["filter"])


@app.post("/api/filter")
def api_filter():
    state["filter"] = int((request.get_json(silent=True) or {}).get("index", 0)) % FILTER_COUNT
    return api_filters()


@app.get("/api/cameras")
def api_cameras():
    return jsonify(list=CAMERAS, current=cam.index)


@app.post("/api/camera")
def api_camera():
    i = int((request.get_json(silent=True) or {}).get("index", 0))
    if state["phase"] == "idle" and 0 <= i < len(CAMERAS):
        cam.switch(i)
    return api_cameras()


@app.post("/api/start")
def api_start():
    ok, err = begin((request.get_json(silent=True) or {}).get("template"))
    return (jsonify(ok=True), 200) if ok else (jsonify(error=err), 409)


@app.post("/api/restart")
def api_restart():
    ok, err = begin(state["template"])
    return (jsonify(ok=True), 200) if ok else (jsonify(error=err), 409)


@app.post("/api/retake")
def api_retake():
    i = int((request.get_json(silent=True) or {}).get("index", -1))
    if not 0 <= i < len(S["photos"]):
        return jsonify(error="foto tidak ada"), 400
    ok, err = begin(state["template"], only=i)
    return (jsonify(ok=True), 200) if ok else (jsonify(error=err), 409)


@app.post("/api/cancel")
def api_cancel():
    cancel_flag.set()
    return jsonify(ok=True)


@app.post("/api/reset")
def api_reset():
    if state["phase"] == "result":
        state.update(phase="idle", upload="", link=None, **{"print": ""})
    return jsonify(ok=True)


@app.post("/api/upload")
def api_upload():
    if state["phase"] != "result" or state["upload"] == "uploading":
        return jsonify(error="tidak bisa"), 409
    state.update(upload="uploading")
    threading.Thread(target=do_upload, daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/print")
def api_print():
    if state["phase"] != "result" or state["print"] == "printing":
        return jsonify(error="tidak bisa"), 409
    state.update(**{"print": "printing"})
    threading.Thread(target=do_print, daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/ack_print")
def api_ack_print():
    state.update(**{"print": ""})
    return jsonify(ok=True)


@app.get("/frame.jpg")
def frame_jpg():
    r = send_file(os.path.join(S["dir"], ".preview.jpg"), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/video.mp4")
def video_mp4():
    return send_file(os.path.join(S["dir"], "video.mp4"), mimetype="video/mp4", conditional=True)


@app.get("/api/qr")
def api_qr():
    import qrcode
    if not state["link"]:
        abort(404)
    buf = io.BytesIO()
    qrcode.make(state["link"]).save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.get("/stream")
def stream():
    def gen():
        while True:
            f = cam.get()
            if f is not None:
                f = cv2.resize(f, (960, int(960 * f.shape[0] / f.shape[1])))
                ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 75])
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            time.sleep(1 / 15)
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--fake", action="store_true", help="tanpa kamera asli")
    a = ap.parse_args()
    CAMERAS = probe_cameras(a.fake)
    cam = Camera(a.camera, a.fake)
    print(f"\nBuka di tablet:  http://{lan_ip()}:{a.port}\n")
    app.run(host="0.0.0.0", port=a.port, threaded=True)
