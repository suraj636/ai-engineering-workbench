"""Start the assistant.

    uv run python app.py                  # live -> http://127.0.0.1:8765
    uv run python app.py --source DIR     # headless: replay images or a video

Controls, as tap-to-record buttons in the browser tab:

    TEACH   teach the focused unknown object (tap, speak, tap again)
    ASK     ask where an object was seen (tap, speak, tap again)
    MEMORY  everything it knows, with pictures and a forget button

Quit with Ctrl-C.
"""
import argparse
import ipaddress
import shutil
import time
from pathlib import Path

import cv2

from assistant import config
from assistant.brain.memory import RECOGNIZE_THRESHOLD
from assistant.brain.robot import Robot
from assistant.vision.focus import build_focus


def replay(robot, source):
    """Headless file mode: images or a video.

    Covers the crop and recognition path. Each image is its own scene, so
    focus stickiness never runs here.
    """
    src = Path(source)
    if src.is_dir():
        frames = []
        for p in sorted(src.iterdir()):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                img = cv2.imread(str(p))
                if img is not None:
                    frames += [(p.name, img)] * 4  # satisfy the stability gate
    else:
        cap, frames, i = cv2.VideoCapture(str(src)), [], 0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            frames.append((f"frame{i}", f))
            i += 1
        cap.release()
    now = time.time()
    prev = None
    for name, frame in frames:
        if src.is_dir() and name != prev:
            robot.focus.reset()
            prev = name
        now += 3.0  # spaced past the requery interval
        robot.process_frame(frame, now)
        f = robot.attention
        if f and f.last_query == now:
            verdict = f.label or "UNKNOWN"
            print(f"{name}: {verdict} ({f.score:.3f})")
    robot.close()


def _ip_arg(text):
    """--advertise: the certificate needs a real IP, not a hostname."""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not an IP address, e.g. 192.168.1.20")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="image dir or video file (headless)")
    ap.add_argument("--data", default=str(config.DATA_DIR),
                    help="shard directory")
    ap.add_argument("--camera", type=int, default=config.CAMERA_INDEX)
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 serves the view to a phone on the network")
    ap.add_argument("--advertise", default=None, metavar="ADDR",
                    type=_ip_arg,
                    help="IP to print and put in the certificate when the "
                         "detected LAN address is not the right one")
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--threshold", type=float,
                    default=RECOGNIZE_THRESHOLD,
                    help=f"recognition threshold (.env RECOGNIZE_THRESHOLD, "
                         f"currently {RECOGNIZE_THRESHOLD})")
    ap.add_argument("--conf", type=float, default=None,
                    help="detector confidence floor (.env DETECT_CONF)")
    ap.add_argument("--max-area", type=float, default=None,
                    help="biggest proposal kept, as a frame fraction")
    ap.add_argument("--location", default=config.WHERE,
                    help='place stamped on new memories, e.g. "desk"')
    ap.add_argument("--no-detector", action="store_true",
                    help="always use the centre crop, even if YOLOE is "
                         "installed")
    ap.add_argument("--reset", action="store_true",
                    help="wipe the shard directory before starting")
    args = ap.parse_args()

    if args.reset and Path(args.data).exists():
        shutil.rmtree(args.data)
        print(f"reset: cleared {args.data}")

    focus = build_focus(conf=args.conf, max_area=args.max_area,
                        prefer_detector=not args.no_detector)
    robot = Robot(data_dir=args.data, threshold=args.threshold, focus=focus,
                  where=args.location)
    if args.source:
        replay(robot, args.source)
    else:
        from assistant.web.server import App
        App(robot, args.camera).run(args.host, args.port,
                                    advertise=args.advertise)


if __name__ == "__main__":
    main()
