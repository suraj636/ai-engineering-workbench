"""Settings loaded once from `.env`.

Exported environment variables beat the file. Every value here is a knob the
rest of the app reads directly, so a bad string fails at import with a legible
message instead of deep inside OpenCV.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# `config.py` sits inside `assistant/`; paths that users own (the .env file,
# the shard and the certificate) stay at the project root beside `app.py`.
PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = PACKAGE_DIR.parent
ENV_FILE = ROOT / ".env"

load_dotenv(ENV_FILE, override=False)


def _number(name, default, cast=float):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise SystemExit(f"{name} in .env must be a number, got {raw!r}")


def _fractions(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) == 1:
        parts *= 4
    try:
        vals = tuple(float(p) for p in parts)
    except ValueError:
        raise SystemExit(
            f"{name} in .env must be one number or four "
            f"(left,top,right,bottom), got {raw!r}")
    if len(vals) != 4 or not all(0 <= v < 0.5 for v in vals):
        raise SystemExit(
            f"{name} wants four fractions of 0 to 0.5, got {raw!r}")
    return vals


# -- recognition -------------------------------------------------------------

# Minimum CLIP cosine similarity for a match. Not a confidence percentage:
# unrelated same-camera objects often score 0.75-0.85. Calibrate per camera.
RECOGNIZE_THRESHOLD = _number("RECOGNIZE_THRESHOLD", 0.90)

# -- detection (only used when the optional detector is installed) -----------

DETECT_CONF = _number("DETECT_CONF", 0.30)
DETECT_MAX_AREA = _number("DETECT_MAX_AREA", 0.20)
DETECT_MIN_AREA = _number("DETECT_MIN_AREA", 0.001)

# -- camera ------------------------------------------------------------------

CAMERA_INDEX = _number("CAMERA_INDEX", 0, int)
CAMERA_ROTATE = _number("CAMERA_ROTATE", 0, int)
if CAMERA_ROTATE not in (0, 180):
    raise SystemExit(f"CAMERA_ROTATE must be 0 or 180, got {CAMERA_ROTATE}")

# Fractions removed from each edge to hide a lens rim.
FRAME_CROP = _fractions("FRAME_CROP", (0.0, 0.0, 0.0, 0.0))
if FRAME_CROP[0] + FRAME_CROP[2] > 0.8 or FRAME_CROP[1] + FRAME_CROP[3] > 0.8:
    raise SystemExit(f"FRAME_CROP cuts away almost the whole frame: {FRAME_CROP}")

# -- server and storage ------------------------------------------------------

PORT = _number("PORT", 8765, int)

# Place stamped on new memories, e.g. WHERE=desk. Recall reads it back.
WHERE = os.environ.get("WHERE", "").strip() or None

DATA_DIR = ROOT / "edge-data"
CERT_DIR = ROOT / "cert"
