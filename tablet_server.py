"""Server tablet photobooth: laptop = kamera + logika, tablet = layar sentuh (browser).

Jalankan:  python tablet_server.py            (kamera asli)
           python tablet_server.py --fake     (tanpa kamera, tes koneksi)
"""
import argparse, base64, io, json, math, os, shutil, socket, subprocess, threading, time
import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_file, send_from_directory, abort
from PIL import Image
from filter_engine import apply_filter, get_filter_name, FILTER_COUNT

ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(ROOT, "assets", "templates")
UI_DIR = os.path.join(ROOT, "tablet_ui")
OUT_DIR = os.path.join(ROOT, "riwayat")
COUNTDOWN = 5       # detik per foto
VIDEO_SECONDS = 5     # video = N detik terakhir sebelum jepret
VIDEO_FPS = 30
OUTPUT_SCALE = 4         # foto hasil = ukuran template x 4
VIDEO_MAX_SIZE = (1080, 1920)  # batas ukuran video (lebar, tinggi) agar mulus di tablet
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
    def __init__(self, index, fake, backend="auto"):
        self.index, self.fake, self.backend, self.frame = index, fake, backend, None
        self.ok = fake  # True jika kamera benar-benar mengirim gambar
        self.lock, self.reopen = threading.Lock(), threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def switch(self, index):
        self.index = index
        self.reopen.set()

    def _try(self, backend, force):
        cap = cv2.VideoCapture(self.index) if backend is None else cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap.release()
            return None
        if force:  # webcam biasa: minta MJPG 1080p. Kamera virtual sering menolak -> dicoba lagi tanpa ini
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        for _ in range(15):  # pastikan frame benar-benar berisi gambar (bukan hitam)
            ok, f = cap.read()
            if ok and f is not None and f.max() > 25:
                return cap
            time.sleep(0.1)
        cap.release()
        return None

    def _open(self):
        if self.fake:
            return None
        if os.name == "nt":
            names = {"dshow": [cv2.CAP_DSHOW], "msmf": [cv2.CAP_MSMF], "auto": [cv2.CAP_DSHOW, cv2.CAP_MSMF]}
            backends = names[self.backend]
        else:
            backends = [None]
        for be in backends:
            for force in (True, False):
                cap = self._try(be, force)
                if cap is not None:
                    print(f"Kamera {self.index}: {int(cap.get(3))} x {int(cap.get(4))} "
                          f"(backend={be}, paksa_format={force})")
                    self.ok = True
                    return cap
        self.ok = False
        print(f"! Kamera {self.index} tidak mengirim gambar. Tutup aplikasi lain yang memakai kamera, "
              "cek mode/koneksi kamera, atau coba --backend msmf / --camera nomor lain.")
        return None

    def _placeholder(self):
        f = np.full((720, 1280, 3), 40, np.uint8)
        cv2.putText(f, "Kamera tidak terbaca", (260, 340), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 4)
        cv2.putText(f, f"Kamera {self.index} - cek koneksi", (320, 420), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (200, 200, 200), 2)
        return f

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
            elif self.fake:
                t += 1
                f = np.zeros((720, 1280, 3), np.uint8)
                f[:] = (40 + t % 120, 90, 160)
                cv2.putText(f, time.strftime("%H:%M:%S"), (380, 400), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
                time.sleep(1 / 20)
            else:  # kamera belum terbaca: tampilkan pesan, coba buka lagi tiap beberapa detik
                f = self._placeholder()
                time.sleep(3)
                with self.lock:
                    self.frame = f
                cap = self._open()
                continue
            with self.lock:
                self.frame = f

    def get(self, filtered=True):
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        return apply_filter(f, state["filter"]) if (f is not None and filtered) else f


def probe_cameras(fake, current=0):
    """Daftar nomor kamera yang ada (nomor asli, bukan urutan)."""
    if fake:
        return [0]
    found = []
    for i in range(6):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(i)
        if cap.isOpened():
            found.append(i)
        cap.release()
    if current not in found:
        found.append(current)
    return sorted(found)


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


PRINT_SEL = {"printer": ""}  # pilihan terakhir operator (hanya untuk dipilih-awal di pop up)


def do_print(printer, copies):
    try:
        from print_manager import PrintManager
        ok = PrintManager.print_image(os.path.join(S["dir"], "foto_frame.jpg"), printer_name=printer, copies=copies)
        state.update(**{"print": "done" if ok else "error"})
    except Exception:
        state.update(**{"print": "error"})


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
    return jsonify(list=[{"index": i, "name": f"Kamera {i}"} for i in CAMERAS], current=cam.index, ok=cam.ok)


@app.post("/api/camera")
def api_camera():
    i = int((request.get_json(silent=True) or {}).get("index", -1))
    if state["phase"] == "idle" and i in CAMERAS:
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


@app.get("/api/printers")
def api_printers():
    try:
        from print_manager import PrintManager
        names = PrintManager.list_printers()
    except Exception:
        names = []
    return jsonify(list=names, selected=PRINT_SEL["printer"])


@app.get("/api/print_preview")
def api_print_preview():
    """Pratinjau hasil cetak sesuai printer terpilih (ukuran halaman, area cetak, strip dobel)."""
    if state["phase"] != "result" or not S["dir"]:
        return jsonify(error="belum ada hasil"), 409
    from print_manager import PrintManager, WIN32_OK, load_config, prepare_image, area_box
    printer = request.args.get("printer", "")
    paper, dpi, info = (1200, 1800), (300, 300), "Perkiraan kertas 4R. Pilih printer untuk ukuran sebenarnya."
    if printer and WIN32_OK:
        try:
            pi = PrintManager.paper_info(printer)
            if pi:
                paper, dpi, info = pi["px"], pi["dpi"], f'Halaman {pi["mm"][0]} × {pi["mm"][1]} mm'
        except Exception:
            info = "Ukuran kertas printer tidak terbaca, memakai perkiraan 4R."
    cfg = load_config()
    ax0, ay0, ax1, ay1 = area_box(paper[0], paper[1], dpi[0], dpi[1], cfg)
    src = Image.open(os.path.join(S["dir"], ".preview.jpg"))
    img, box = prepare_image(src, ax1 - ax0, ay1 - ay0, cfg)
    box = (box[0] + ax0, box[1] + ay0, box[2] + ax0, box[3] + ay0)
    area_on = bool((cfg.get("area") or {}).get("enabled"))
    if area_on:  # tampilkan hanya kertas potongan (area cetak), diperbesar; posisi di halaman ditulis di teks
        aw, ah = ax1 - ax0, ay1 - ay0
        sc = min(600 / aw, 800 / ah)
        canvas = Image.new("RGB", (max(1, round(aw * sc)), max(1, round(ah * sc))), "white")
        bx = [round(v * sc) for v in (box[0] - ax0, box[1] - ay0, box[2] - ax0, box[3] - ay0)]
        canvas.paste(img.resize((max(1, bx[2] - bx[0]), max(1, bx[3] - bx[1])), Image.LANCZOS), (bx[0], bx[1]))
        a = cfg["area"]
        nama = {"left": "kiri", "right": "kanan", "center": "tengah", "top": "atas", "bottom": "bawah"}
        pos = f'{nama.get(a.get("align_y", "top"), "?")}-{nama.get(a.get("align_x", "center"), "?")}'
        geser = f' (geser {a.get("offset_x_mm", 0)}, {a.get("offset_y_mm", 0)} mm)' if a.get("offset_x_mm") or a.get("offset_y_mm") else ""
        info += f" · area {a.get('width_mm', 102)}×{a.get('height_mm', 152)} mm, posisi di halaman: {pos}{geser}"
    else:
        sc = min(600 / paper[0], 800 / paper[1])
        canvas = Image.new("RGB", (max(1, round(paper[0] * sc)), max(1, round(paper[1] * sc))), "white")
        x0, y0, x1, y1 = [round(v * sc) for v in box]
        canvas.paste(img.resize((max(1, x1 - x0), max(1, y1 - y0)), Image.LANCZOS), (x0, y0))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=85)
    dobel = bool(cfg.get("duplicate_strip")) and src.width / src.height <= 0.4
    return jsonify(image="data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), info=info, dobel=dobel)


@app.post("/api/print")
def api_print():
    """Cetak manual: printer WAJIB dipilih operator (tidak ada printer otomatis)."""
    body = request.get_json(silent=True) or {}
    printer, copies = body.get("printer", ""), max(1, min(5, int(body.get("copies", 1))))
    if state["phase"] != "result" or state["print"] == "printing":
        return jsonify(error="tidak bisa"), 409
    if printer not in api_printers().json["list"]:
        return jsonify(error="printer belum dipilih"), 400
    PRINT_SEL["printer"] = printer
    state.update(**{"print": "printing"})
    threading.Thread(target=do_print, args=(printer, copies), daemon=True).start()
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
    ap.add_argument("--backend", choices=["auto", "dshow", "msmf"], default="auto", help="cara membuka kamera (Windows)")
    a = ap.parse_args()
    CAMERAS = probe_cameras(a.fake, a.camera)
    cam = Camera(a.camera, a.fake, a.backend)
    print(f"\nBuka di tablet:  http://{lan_ip()}:{a.port}\n")
    app.run(host="0.0.0.0", port=a.port, threaded=True)
