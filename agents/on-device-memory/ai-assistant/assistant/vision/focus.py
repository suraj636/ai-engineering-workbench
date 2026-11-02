"""Decide what the camera is looking at.

Two interchangeable front ends:

    CenterFocus  no model: whatever sits in the middle of the frame is the
                 subject. Everything works, including teaching and recall.
    YoloFocus    YOLOE + BoT-SORT, the reference behaviour, when the optional
                 `detect` extra is installed.

Both return `Target` objects with a box, a crop, and the bookkeeping the
recognition loop needs.
"""
import os
import time

import cv2
import numpy as np

from .. import config

# Never let a missing dependency turn into a pip call mid-demo.
os.environ.setdefault("YOLO_AUTOINSTALL", "false")

PAD = 0.12          # crop margin; the segmentation mask removes the rest
FILL = (124, 124, 124)
STABLE_FRAMES = 3   # passes before a track is displayed or teachable
REQUERY_SECONDS = 2.0
DEAD_SECONDS = 5.0
IMGSZ = 640
MAX_DET = 64


def padded_crop(frame, box, mask=None):
    """The crop that gets embedded: 12% margin, background flattened to gray.

    Filling outside the segmentation mask is what makes recognition survive a
    changed background, or a hand holding the object.
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * PAD, (y2 - y1) * PAD
    x1, y1 = max(0, int(x1 - px)), max(0, int(y1 - py))
    x2, y2 = min(w, int(x2 + px)), min(h, int(y2 + py))
    region = frame[y1:y2, x1:x2]
    if mask is not None and len(mask) >= 3 and region.size:
        full = np.zeros((h, w), np.uint8)
        cv2.fillPoly(full, [np.asarray(mask, dtype=np.int32)], 255)
        # Grow the mask slightly: the segmentation edge cuts just inside the
        # object, and filling right up to it shaves off its outline.
        full = cv2.dilate(full, np.ones((7, 7), np.uint8), iterations=1)
        inside = full[y1:y2, x1:x2] > 0
        region = region.copy()
        region[~inside] = FILL
    return region


def crop_quality(frame, box, conf):
    """How good a portrait this crop is: bigger, sharper, surer."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return 0.0
    small = cv2.resize(frame[y1:y2, x1:x2], (96, 96),
                       interpolation=cv2.INTER_AREA)
    sharp = cv2.Laplacian(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY),
                          cv2.CV_32F).var()
    area = (x2 - x1) * (y2 - y1) / (w * h)
    return conf * (area ** 0.5) * min(sharp, 1200.0)


class Target:
    """One thing in view, and what memory said about it."""

    def __init__(self, tid, box, crop):
        self.tid = tid
        self.box = box
        self.crop = crop
        self.frames = 0
        self.last_seen = 0.0
        self.last_query = 0.0
        self.crop_q = 0.0
        self.salience = 1.0
        self.label = None    # from memory, never from a detector
        self.guess = None    # near match, display only
        self.note = None     # the taught transcript
        self.thumb = None    # the matched taught view
        self.score = 0.0
        self.vec = None
        self.sighted = False

    @property
    def stable(self):
        return self.frames >= STABLE_FRAMES

    def due_for_query(self, now):
        return self.stable and now - self.last_query >= REQUERY_SECONDS

    def requery_now(self):
        self.last_query = time.time() - REQUERY_SECONDS


class CenterFocus:
    """The centred object. No model, no download, no torch.

    The crop covers the middle 45% of the shorter side, which measured well on
    the reference test images: views of one object scored 0.91-0.96 against a
    best foreign score of 0.57.
    """

    name = "centre crop"
    loaded = True

    def __init__(self, size=0.45):
        self.size = size
        self.target = Target(0, (0, 0, 0, 0), None)
        self.frames = 0

    def reset(self):
        self.target = Target(0, (0, 0, 0, 0), None)
        self.frames = 0

    def process(self, frame, now=None):
        now = now or time.time()
        h, w = frame.shape[:2]
        side = int(min(h, w) * self.size)
        cx, cy = w // 2, h // 2
        box = (cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)
        self.frames = min(self.frames + 1, STABLE_FRAMES + 1)
        t = self.target
        t.box = box
        t.crop = padded_crop(frame, box)   # always the newest frame
        t.frames = self.frames
        t.last_seen = now
        return [t]


class YoloFocus:
    """YOLOE + BoT-SORT tracking + the stability gate."""

    name = "YOLOE tracking"
    loaded = True

    def __init__(self, weights="yoloe-11l-seg-pf.pt",
                 conf=config.DETECT_CONF, max_area=config.DETECT_MAX_AREA):
        from pathlib import Path

        import torch
        from ultralytics import YOLO

        self.conf = conf
        self.max_area = max_area
        repo_copy = config.ROOT / weights
        if not Path(weights).exists() and repo_copy.exists():
            weights = str(repo_copy)
        self.model = YOLO(weights)
        if torch.cuda.is_available():
            self.device = "cuda"
        elif torch.backends.mps.is_available():
            self.device = "mps"
        else:
            self.device = "cpu"
        # Half precision pays off on CUDA only; unmeasured elsewhere.
        self.quantize = 16 if self.device == "cuda" else None
        self.tracks = {}
        self._next_id = 0
        self._shape = None      # frame size the tracker is currently using

    def warm(self):
        dummy = np.zeros((360, 640, 3), dtype=np.uint8)
        self.model.predict(dummy, device=self.device, imgsz=IMGSZ,
                           quantize=self.quantize, verbose=False)

    def reset(self):
        self.tracks.clear()
        predictor = getattr(self.model, "predictor", None)
        for tracker in getattr(predictor, "trackers", None) or []:
            tracker.reset()

    def process(self, frame, now=None):
        now = now or time.time()
        h, w = frame.shape[:2]
        # Switching cameras changes the resolution. The tracker's motion
        # compensation caches the previous frame and cannot compare two sizes,
        # so it would warn on every pass. Start a fresh tracker instead.
        if (h, w) != self._shape:
            if self._shape is not None:
                self.reset()
            self._shape = (h, w)
        results = self.model.track(
            frame,
            device=self.device,
            quantize=self.quantize,
            conf=self.conf,
            imgsz=IMGSZ,
            max_det=MAX_DET,
            agnostic_nms=True,
            persist=True,
            verbose=False,
            tracker="botsort.yaml",
        )[0]
        seen_tids = set()
        boxes = results.boxes
        polys = results.masks.xy if results.masks is not None else None
        if boxes is not None and boxes.id is not None:
            rows = zip(boxes.id.int().tolist(), boxes.conf.tolist(),
                       boxes.xyxy.tolist())
            for i, (tid, conf, box) in enumerate(rows):
                x1, y1, x2, y2 = box
                area = (x2 - x1) * (y2 - y1) / (w * h)
                if not config.DETECT_MIN_AREA <= area <= self.max_area:
                    continue
                t = self.tracks.get(tid)
                if t is None:
                    t = self.tracks[tid] = Target(tid, box, None)
                t.frames = min(t.frames + 1, STABLE_FRAMES + 1)
                t.last_seen = now
                t.box = box
                # Attention: size x centrality, so an object held in the middle
                # beats larger off-centre clutter.
                cx = (x1 + x2) / 2 / w - 0.5
                cy = (y1 + y2) / 2 / h - 0.5
                t.salience = (area ** 0.5) * (1 - (cx * cx + cy * cy) ** 0.5)
                q = crop_quality(frame, box, conf)
                if q >= t.crop_q or t.crop is None:
                    mask = (polys[i] if polys is not None and i < len(polys)
                            else None)
                    t.crop = padded_crop(frame, box, mask)
                    t.crop_q = q
                seen_tids.add(tid)

        for tid, t in list(self.tracks.items()):
            if tid not in seen_tids:
                if now - t.last_seen > DEAD_SECONDS:
                    del self.tracks[tid]
                else:
                    # Decay gradually so one missed detection does not flicker.
                    t.frames = max(0, t.frames - 1)

        return [t for t in self.tracks.values() if t.stable]


def build_focus(conf=None, max_area=None, prefer_detector=True):
    """YOLOE when it is installed, otherwise the centre crop."""
    if prefer_detector:
        opts = {}
        if conf is not None:
            opts["conf"] = conf
        if max_area is not None:
            opts["max_area"] = max_area
        try:
            detector = YoloFocus(**opts)
            print(f"focus: {detector.name} ({detector.device})")
            return detector
        except ImportError:
            print("focus: centre crop (install the detector extra for YOLOE: "
                  "uv sync --extra detect)")
    else:
        print("focus: centre crop (--no-detector)")
    return CenterFocus()
