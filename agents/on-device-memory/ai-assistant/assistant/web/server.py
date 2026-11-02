"""The web server: one page, one MJPEG stream, a handful of routes.

Two worker threads, not four: one reads and draws the camera, one runs
detection. Requests are served by ThreadingHTTPServer, so a recording POST
never blocks the feed.
"""
import ipaddress
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import wave
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np

from .. import config
from ..brain import models
from ..brain.robot import answer_line, stamp
from ..vision.draw import draw_feed, splash

PAGE = (Path(__file__).resolve().parent / "page.html").read_bytes()
STREAM_QUALITY = 80
CROP_PX = 240
DETECT_EVERY = 0.15     # seconds between detection passes
PHONE_FRAME_TIMEOUT = 2.0  # a phone frame this fresh replaces the laptop camera
SILENCE_RMS = 60.0      # below this the recording is treated as empty
TARGET_RMS = 1800.0     # quiet phone audio is lifted to this before Whisper
MAX_GAIN = 15.0         # cap the boost so background noise is not blown up
PEAK = 32000.0          # leave a little headroom when amplifying


def crop_edges(frame):
    """Remove the configured lens rim from a live camera frame."""
    left, top, right, bottom = config.FRAME_CROP
    if not any(config.FRAME_CROP):
        return frame
    h, w = frame.shape[:2]
    x1, y1 = int(w * left), int(h * top)
    x2, y2 = w - int(w * right), h - int(h * bottom)
    return frame[y1:y2, x1:x2].copy()


def wav_rms(path):
    """Mean level of a WAV file, for rejecting an empty recording."""
    with wave.open(str(path)) as w:
        frames = w.readframes(w.getnframes())
    a = np.frombuffer(frames, np.int16).astype(np.float32)
    return float(np.sqrt((a * a).mean())) if a.size else 0.0


def amplify_wav(path, level=None):
    """Lift a quiet recording before transcription.

    A phone held at arm's length records speech around RMS 100, which Whisper
    treats as silence or noise. Normalising to a normal speech level fixes it.
    Rewrites the 16-bit PCM WAV in place and returns the gain applied.
    """
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    if params.sampwidth != 2 or not frames:
        return 1.0
    a = np.frombuffer(frames, np.int16).astype(np.float32)
    rms = float(np.sqrt((a * a).mean())) if level is None else level
    if rms <= 0:
        return 1.0
    gain = min(MAX_GAIN, TARGET_RMS / rms)
    peak = float(np.abs(a).max()) if a.size else 0.0
    if peak > 0:
        gain = min(gain, PEAK / peak)   # clipping is worse than a quiet clip
    if gain <= 1.0:
        return 1.0
    a = np.clip(a * gain, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(a.tobytes())
    return gain


def lan_ip():
    """The address a phone on the same network should use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert(ip, root=None):
    """Create or reuse the self-signed certificate a phone needs for its mic.

    Phones only allow microphone access on a secure context, and a LAN address
    over plain HTTP is not one. The certificate is a CA certificate so it can
    be installed and trusted on the phone for good.
    """
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    root = Path(root) if root is not None else config.CERT_DIR
    cert, key, named = root / "cert.pem", root / "key.pem", root / "names.txt"
    hostname = socket.gethostname().split(".")[0] + ".local"
    want = ",".join([f"IP:{ip}", "IP:127.0.0.1", "DNS:localhost",
                     f"DNS:{hostname}"])
    if (cert.exists() and key.exists() and named.exists()
            and named.read_text(encoding="utf-8") == want):
        return str(cert), str(key)

    root.mkdir(exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "on-device-memory-assistant"),
    ])
    now = datetime.now(UTC)
    san = x509.SubjectAlternativeName([
        x509.IPAddress(ipaddress.ip_address(ip)),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
    ])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=397))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                       critical=True)
        .add_extension(san, critical=False)
        .sign(private_key, hashes.SHA256())
    )
    key.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    named.write_text(want, encoding="utf-8")
    print(f"generated a certificate for {ip} (valid 397 days)")
    return str(cert), str(key)


class Handler(BaseHTTPRequestHandler):
    app = None
    cert = None  # path to the served certificate, when running HTTPS

    def log_message(self, *args):
        pass

    def _query(self, key, default=None):
        vals = parse_qs(urlparse(self.path).query).get(key)
        return vals[0] if vals else default

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data, ctype, cache=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _404(self):
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        try:
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(PAGE)
            elif self.path == "/state":
                self._send_json(self.app.state())
            elif self.path.startswith("/memory"):
                self._send_json(self.app.memories())
            elif self.path.startswith("/thumb?f="):
                name = Path(unquote(self.path.split("=", 1)[1])).name
                path = self.app.robot.thumbs / name
                if path.is_file():
                    self._send_bytes(path.read_bytes(), "image/jpeg",
                                     cache="max-age=3600")
                else:
                    self._404()
            elif self.path.startswith("/crop.jpg"):
                jpeg = self.app.crop_jpeg()
                if jpeg:
                    self._send_bytes(jpeg, "image/jpeg")
                else:
                    self._404()
            elif self.path.startswith("/stream"):
                self._stream()
            elif self.path == "/cert.crt" and self.cert:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-x509-ca-cert")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="memory-assistant.crt"')
                self.end_headers()
                self.wfile.write(Path(self.cert).read_bytes())
            else:
                self._404()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
            pass  # tab closed or refreshed mid-stream

    def _stream(self):
        """Always send the newest composed frame, so a slow client loses frames
        instead of accumulating lag."""
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        last = -1
        while not self.app.stop.is_set():
            shot = self.app.shot
            if shot is None or shot[0] == last:
                time.sleep(0.01)
                continue
            last, jpeg = shot
            self.wfile.write(
                b"--frame\r\nContent-Type: image/jpeg\r\n"
                + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                + jpeg + b"\r\n")

    def do_POST(self):
        try:
            if self.path.startswith("/audio"):
                action = "t" if self.path.endswith("=t") else "a"
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                self.send_response(self.app.on_audio(action, body))
                self.end_headers()
            elif self.path.startswith("/forget"):
                label = self._query("label")
                self._send_json(
                    {"label": label,
                     "n": self.app.forget(label) if label else 0},
                    200 if label else 400)
            elif self.path.startswith("/frame"):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                self.send_response(self.app.on_frame(body))
                self.end_headers()
            else:
                self._404()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
            pass


class App:
    def __init__(self, robot, camera=0):
        self.robot = robot
        self.camera = camera
        self.cap = None
        self.lock = threading.RLock()  # robot state shared by three threads
        self.latest = None             # newest camera frame
        self.tracks = []               # newest recognition results
        self.shot = None               # (seq, jpeg) for /stream
        self._seq = 0
        self.shot_lock = threading.Lock()
        self.phone_at = 0.0            # last JPEG posted by a phone camera
        self.busy = False              # a voice action owns the mic path
        self.card = None               # ("taught"|"answer"|"forgot", data)
        self.card_seq = 0              # bumps when a new card is set
        self.mem_count = 0
        self.frame_at = None
        self.stop = threading.Event()
        self._banner = None
        self._banner_seq = 0

    @property
    def banner(self):
        return self._banner

    @banner.setter
    def banner(self, msg):
        self._banner = msg
        self._banner_seq += 1

    # -- threads ---------------------------------------------------------------

    def _camera_loop(self):
        while not self.stop.is_set():
            # A phone is posting frames, so leave the laptop camera alone.
            if time.monotonic() - self.phone_at < PHONE_FRAME_TIMEOUT:
                time.sleep(0.03)
                continue
            ok, frame = self.cap.read()
            if not ok:
                print("the camera stopped delivering frames; shutting down.")
                self.stop.set()
                break
            if config.CAMERA_ROTATE == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            frame = crop_edges(frame)
            self.latest = frame
            self.frame_at = time.monotonic()
            self._render(frame, list(self.tracks), self.robot.attention)

    def _detect_loop(self):
        last = 0.0
        while not self.stop.is_set():
            if (self.latest is None or self.busy
                    or time.time() - last < DETECT_EVERY):
                time.sleep(0.01)
                continue
            frame = self.latest.copy()
            last = time.time()
            with self.lock:
                self.tracks = self.robot.process_frame(frame)
                self.mem_count = self.robot.memory.count()

    def _render(self, frame, targets, focused):
        view = draw_feed(frame.copy(), targets, focused)
        ok, buf = cv2.imencode(".jpg", view,
                               [cv2.IMWRITE_JPEG_QUALITY, STREAM_QUALITY])
        if ok:
            self._publish(buf.tobytes())

    def _publish(self, jpeg):
        with self.shot_lock:
            self._seq += 1
            self.shot = (self._seq, jpeg)

    def on_frame(self, body):
        """A JPEG from a browser's camera. The laptop still does the seeing."""
        if not body:
            return 400
        if len(body) > 8_000_000:
            return 413
        frame = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return 400
        now = time.monotonic()
        self.latest = frame
        self.frame_at = now
        self.phone_at = now
        self._render(frame, list(self.tracks), self.robot.attention)
        return 200

    def _splash(self, msg, sub="first run downloads ~1.1 GB of models"):
        print(msg)
        ok, buf = cv2.imencode(".jpg", splash(msg, sub))
        if ok:
            self._publish(buf.tobytes())

    # -- voice -----------------------------------------------------------------

    def on_audio(self, action, body):
        """Accept a browser recording. Returns an HTTP status."""
        with self.lock:
            if self.busy:
                return 409
            target = None
            if action == "t":
                t = self.robot.teachable
                if t is None or t.crop is None:
                    self.banner = "nothing new in focus to teach"
                    return 409
                frame = self.latest.copy() if self.latest is not None else None
                target = (t.crop.copy(), frame, t.box, t)
            self.busy = True
            self.banner = "thinking..."
        threading.Thread(target=self._voice, args=(action, body, target),
                         daemon=True).start()
        return 202

    def _voice(self, action, body, target):
        path = (Path(tempfile.gettempdir())
                / f"memory-assistant-{os.getpid()}.wav")
        try:
            path.write_bytes(body)
            level = wav_rms(path)
            print(f"recorded level (rms): {level:.0f}")
            if level < SILENCE_RMS:
                self.banner = "didn't hear anything, try again"
                return
            gain = amplify_wav(path, level)
            if gain > 1.0:
                print(f"amplified x{gain:.1f} for transcription")
            self.banner = "transcribing..."
            question = models.transcribe(str(path))
            print(f"heard: {question!r}")
            if action == "t":
                crop, frame, box, track = target
                with self.lock:
                    taught = self.robot.teach(crop, question,
                                              frame=frame, box=box)
                    self.mem_count = self.robot.memory.count()
                    track.requery_now()
                self.card = ("taught", taught)
                self.card_seq += 1
                self.banner = f'taught: "{taught["label"]}"'
            else:
                res = self.robot.ask(question)
                self.card = ("answer", (question, res))
                self.card_seq += 1
                self.banner = answer_line(res)
        except Exception as e:  # noqa: BLE001 - keep the app alive
            print(f"voice action failed: {e}")
            self.banner = "voice action failed - see the terminal"
        finally:
            try:
                path.unlink()
            except OSError:
                pass
            with self.lock:
                self.busy = False

    # -- what the page reads ----------------------------------------------------

    def state(self):
        f = self.robot.attention
        return {
            "count": self.mem_count,
            "threshold": self.robot.memory.threshold,
            "busy": self.busy,
            "status": self.banner,
            "status_seq": self._banner_seq,
            "teachable": self.robot.teachable is not None,
            "focus": {
                "label": f.label,
                "guess": f.guess,
                "score": round(f.score, 3),
                "note": f.note,
                "thumb": Path(f.thumb).name if f.thumb else None,
            } if f is not None and f.last_query else None,
            "card": self._card_json(),
            "card_seq": self.card_seq,
            "frame": ({"w": int(self.latest.shape[1]), "h": int(self.latest.shape[0])}
                      if self.latest is not None else None),
            "events": self.robot.events[-3:],
            "phone_camera": time.monotonic() - self.phone_at
                            < PHONE_FRAME_TIMEOUT,
        }

    def _card_json(self):
        if not self.card:
            return None
        kind, data = self.card
        if kind == "taught":
            return {"kind": "taught", "label": data["label"],
                    "transcript": data["transcript"],
                    "where": self.robot.memory.where}
        if kind == "forgot":
            return {"kind": "forgot", "label": data["label"], "n": data["n"]}
        question, res = data
        return {
            "kind": "answer", "q": question, "say": answer_line(res),
            "hits": [{
                "label": hit.payload.get("label"),
                "when": stamp(hit.payload.get("ts") or 0),
                "where": hit.payload.get("where"),
                "kind": hit.payload.get("kind"),
                "thumb": Path(hit.payload["thumb"]).name
                         if hit.payload.get("thumb") else None,
            } for hit in res["sightings"]],
        }

    def memories(self):
        objects = self.robot.memory.objects()
        return {
            "total": len(objects),
            "points": self.mem_count,
            "objects": [{
                "label": o["label"],
                "seen": o["seen"],
                "views": [self._view(v) for v in o["views"]],
                "sightings": [self._view(v) for v in o["sightings"]],
            } for o in objects],
        }

    @staticmethod
    def _view(payload):
        thumb = payload.get("thumb")
        scene = payload.get("scene") or thumb
        return {
            "id": str(payload.get("id")),
            "when": stamp(payload.get("ts") or 0),
            "transcript": payload.get("transcript"),
            "where": payload.get("where"),
            "thumb": Path(thumb).name if thumb else None,
            "scene": Path(scene).name if scene else None,
        }

    def crop_jpeg(self):
        focus = self.robot.attention
        crop = focus.crop if focus is not None else None
        if crop is None or not crop.size:
            return None
        h, w = crop.shape[:2]
        scale = CROP_PX / max(h, w)
        if scale < 1:
            crop = cv2.resize(crop, (max(1, int(w * scale)),
                                     max(1, int(h * scale))))
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def forget(self, label):
        with self.lock:
            n = self.robot.forget(label)
            self.mem_count = self.robot.memory.count()
        self.card = ("forgot", {"label": label, "n": n})
        self.card_seq += 1
        self.banner = f'forgot "{label}"'
        return n

    # -- startup ---------------------------------------------------------------

    def run(self, host="127.0.0.1", port=config.PORT, advertise=None):
        sys.stdout.reconfigure(line_buffering=True)
        backend = (cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY)
        self.cap = cv2.VideoCapture(self.camera, backend)
        if sys.platform == "win32" and not self.cap.isOpened():
            self.cap.release()
            self.cap = cv2.VideoCapture(self.camera, cv2.CAP_ANY)
        if not self.cap.isOpened():
            raise SystemExit(
                f"camera {self.camera} did not open. Try another index with "
                "--camera 1, or close other apps that hold the camera.")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        loopback = host in ("127.0.0.1", "localhost", "::1")
        addr = advertise or (host if loopback else lan_ip())

        Handler.app = self
        server = ThreadingHTTPServer((host, port), Handler)
        # A phone microphone needs a secure context, so a LAN run is HTTPS.
        scheme = "http"
        if not loopback:
            try:
                cert, key = ensure_cert(addr)
                Handler.cert = cert
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(cert, key)
                server.socket = ctx.wrap_socket(server.socket, server_side=True)
                scheme = "https"
            except Exception as e:  # noqa: BLE001 - HTTP stays a useful fallback
                print(f"HTTPS setup failed ({e}); serving plain HTTP, so the "
                      "phone microphone will not work.")

        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"{scheme}://{addr}:{port}"
        print(f"live view: {url}   (Ctrl-C stops the assistant)")
        if loopback:
            webbrowser.open(url)
        else:
            print("phone: open that address on the same Wi-Fi, accept the "
                  "certificate warning, and allow the microphone.")
            print(f"to trust it for good: open {url}/cert.crt on the phone")

        try:
            self._splash("loading models...")
            models.warm(lambda name: self._splash(f"loading {name}..."))
            if hasattr(self.robot.focus, "warm"):
                self._splash("loading YOLOE detector...", "")
                self.robot.focus.warm()
            self.mem_count = self.robot.memory.count()
            self._splash("ready", "the camera opens in a moment")
            threading.Thread(target=self._camera_loop, daemon=True).start()
            threading.Thread(target=self._detect_loop, daemon=True).start()
            while not self.stop.is_set():
                time.sleep(0.3)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            server.shutdown()
            if self.cap is not None:
                self.cap.release()
            with self.lock:
                self.robot.close()
