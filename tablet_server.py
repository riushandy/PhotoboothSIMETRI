"""Prototipe: laptop = server kamera, tablet = layar sentuh (browser).

Jalankan:  python tablet_server.py            (kamera asli)
           python tablet_server.py --fake     (tanpa kamera, untuk tes koneksi)
Lalu buka alamat yang tercetak di browser tablet (WiFi yang sama).
"""
import argparse, io, json, os, socket, threading, time
import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_file, send_from_directory, abort
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(ROOT, "assets", "templates")
UI_DIR = os.path.join(ROOT, "tablet_ui")
OUT_DIR = os.path.join(ROOT, "riwayat")
COUNTDOWN = 3  # detik per foto (prototipe: singkat)

app = Flask(__name__)
state_lock = threading.Lock()
state = {"phase": "idle", "count": 0, "shot": 0, "total": 0, "template": None,
         "result": None, "stamp": 0, "upload": "", "link": None, "error": ""}
cancel_flag = threading.Event()


# ── Kamera: satu pemilik, frame dibagikan ke preview & jepret ───────────
class Camera:
    def __init__(self, index, fake):
        self.index, self.fake = index, fake
        self.frame, self.lock = None, threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def _open(self):
        if self.fake:
            return None
        api = cv2.CAP_DSHOW if os.name == "nt" else 0
        cap = cv2.VideoCapture(self.index, api)
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
            if cap is not None:
                ok, f = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
            else:
                t += 1
                f = np.zeros((720, 1280, 3), np.uint8)
                f[:] = (40 + t % 120, 90, 160)
                cv2.putText(f, time.strftime("%H:%M:%S"), (380, 400),
                            cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
                time.sleep(1 / 20)
            with self.lock:
                self.frame = f

    def get(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()


# ── Template & komposit ─────────────────────────────────────────────────
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
    W, H = cfg["width"], cfg["height"]
    canvas = Image.new("RGB", (W, H), "white")
    for img, (x, y, w, h) in zip(photos, cfg["layout"]):
        canvas.paste(cover(img, w, h), (x, y))
    ov = Image.open(os.path.join(tdir, "overlay.png")).convert("RGBA")
    if ov.size != (W, H):
        ov = ov.resize((W, H), Image.LANCZOS)
    canvas.paste(ov, (0, 0), ov)
    return canvas


# ── Sesi (satu sesi saja pada satu waktu) ───────────────────────────────
def run_session(tid, total):
    try:
        photos = []
        for i in range(total):
            for n in range(COUNTDOWN, 0, -1):
                state.update(phase="countdown", count=n, shot=i + 1)
                time.sleep(1)
                if cancel_flag.is_set():
                    return
            frame = cam.get()
            photos.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            state.update(phase="flash", count=0)
            time.sleep(0.4)
        state.update(phase="processing")
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, time.strftime("tablet_%Y%m%d_%H%M%S.jpg"))
        compose(photos, tid).save(path, quality=95, subsampling=0, dpi=(300, 300))
        state.update(phase="result", result=path, stamp=time.time())
    except Exception as e:
        state.update(phase="idle", error=f"Sesi gagal: {e}")
    finally:
        if cancel_flag.is_set():
            state.update(phase="idle")


def do_upload():
    try:
        from upload_manager import UploadManager  # kode lama dipakai ulang
        link = UploadManager().upload_file(state["result"])
        if not link:
            raise RuntimeError("tidak ada link")
        state.update(link=link, upload="done")
    except Exception as e:
        state.update(upload="error", error=f"Upload gagal: {e}")


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


@app.post("/api/start")
def api_start():
    tid = (request.get_json(silent=True) or {}).get("template")
    tpl = next((t for t in list_templates() if t["id"] == tid), None)
    if tpl is None:
        return jsonify(error="template tidak ada"), 400
    with state_lock:
        if state["phase"] != "idle":
            return jsonify(error="sesi sedang berjalan"), 409
        cancel_flag.clear()
        state.update(phase="countdown", template=tid, total=tpl["count"], shot=1,
                     result=None, upload="", link=None, error="")
    threading.Thread(target=run_session, args=(tid, tpl["count"]), daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/cancel")
def api_cancel():
    cancel_flag.set()
    return jsonify(ok=True)


@app.post("/api/reset")
def api_reset():
    if state["phase"] == "result":
        state.update(phase="idle", result=None, upload="", link=None, error="")
    return jsonify(ok=True)


@app.post("/api/upload")
def api_upload():
    if state["phase"] != "result" or state["upload"] == "uploading":
        return jsonify(error="tidak bisa"), 409
    state.update(upload="uploading", error="")
    threading.Thread(target=do_upload, daemon=True).start()
    return jsonify(ok=True)


@app.get("/result.jpg")
def result_jpg():
    if not state["result"]:
        abort(404)
    r = send_file(state["result"], mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return r


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
                f = cv2.resize(f, (640, int(640 * f.shape[0] / f.shape[1])))
                ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 70])
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
    cam = Camera(a.camera, a.fake)
    print(f"\nBuka di tablet:  http://{lan_ip()}:{a.port}\n")
    app.run(host="0.0.0.0", port=a.port, threaded=True)
