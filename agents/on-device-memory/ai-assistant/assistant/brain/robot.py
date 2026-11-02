"""Detection, recognition, teaching, and recall: the part with no camera."""
import time
from datetime import date
from pathlib import Path

import cv2

from . import labels, models
from .memory import RECOGNIZE_THRESHOLD, Memory

# A new object must be this much more prominent to take focus. Raising it
# makes focus steadier but harder to move.
FOCUS_MARGIN = 1.6

# The picture kept beside every memory: the box plus a wider margin than the
# recognition crop, unmasked. Nothing is scored against it.
SCENE_PAD = 0.3
SCENE_PX = 480


def stamp(ts):
    """Compact timestamp for the page and the spoken answer."""
    t = time.localtime(ts)
    return f"{time.strftime('%b', t)} {t.tm_mday}, {time.strftime('%H:%M', t)}"


def when(ts):
    """A spoken timestamp: just the clock today, the date on older sightings."""
    t = time.localtime(ts)
    now = time.localtime()
    clock = time.strftime("%I:%M %p", t).lstrip("0")
    if (t.tm_year, t.tm_yday) == (now.tm_year, now.tm_yday):
        return clock
    return f"{time.strftime('%b', t)} {t.tm_mday}, {clock}"


def day_start_ts():
    return time.mktime(date.today().timetuple())  # noqa: DTZ011


def answer_line(res):
    """One sentence a person can read, made only from stored payload fields."""
    if res["inventory"]:
        names = []
        for hit in res["sightings"]:
            label = hit.payload.get("label")
            if label and label not in names:
                names.append(label)
        return ("today: " + ", ".join(names)) if names else "nothing seen today"
    if not res["label"]:
        return "I don't know that one yet"
    hits = res["sightings"]
    if not hits:
        return f"I know {res['label']}, but have not seen it"
    payload = hits[0].payload
    line = f"{res['label']}: {when(payload.get('ts'))}"
    if payload.get("where"):
        line += f" at {payload['where']}"
    return line


class Robot:
    def __init__(self, data_dir="edge-data", threshold=RECOGNIZE_THRESHOLD,
                 focus=None, where=None):
        self.memory = Memory(data_dir, threshold=threshold, where=where)
        self.focus = focus
        self.thumbs = Path(data_dir) / "thumbs"
        self.thumbs.mkdir(parents=True, exist_ok=True)
        self.events = []       # recent memory writes, for the on-screen log
        self.attention = None  # panel subject
        self.teachable = None  # the salient UNKNOWN, the TEACH target
        self._incumbent = {}

    def log(self, msg):
        self.events.append(f"{time.strftime('%H:%M:%S')}  {msg}")
        del self.events[:-8]

    def _thumb(self, crop, tag, quality=None):
        path = self.thumbs / f"{time.time_ns()}_{tag}.jpg"
        opts = [] if quality is None else [cv2.IMWRITE_JPEG_QUALITY, quality]
        cv2.imwrite(str(path), crop, opts)
        return str(path)

    def _scene(self, frame, box):
        """Save the object and some context for the memory panel."""
        if frame is None or not frame.size:
            return None
        h, w = frame.shape[:2]
        img = frame
        if box is not None:
            x1, y1, x2, y2 = box
            px, py = (x2 - x1) * SCENE_PAD, (y2 - y1) * SCENE_PAD
            x1, y1 = max(0, int(x1 - px)), max(0, int(y1 - py))
            x2, y2 = min(w, int(x2 + px)), min(h, int(y2 + py))
            region = frame[y1:y2, x1:x2]
            if region.size:
                img = region
        h, w = img.shape[:2]
        scale = SCENE_PX / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (max(1, int(w * scale)),
                                   max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)
        return self._thumb(img, "scene", quality=80)

    # -- the loop -------------------------------------------------------------

    def process_frame(self, frame, now=None):
        """Detect, embed and match due targets, then pick what to attend to."""
        now = now or time.time()
        tracks = self.focus.process(frame, now)
        live = []
        for t in tracks:
            if t.due_for_query(now):
                t.vec = models.embed_crop(t.crop)
                hit, score, guess = self.memory.recognize(t.vec)
                t.score = score
                t.label = hit.payload["label"] if hit else None
                t.guess = guess          # display only, never recognition
                t.note = hit.payload["transcript"] if hit else None
                t.thumb = hit.payload.get("thumb") if hit else None
                t.last_query = now
                t.crop_q = 0.0
            live.append(t)

        knowns = self._one_per_label([t for t in live if t.label])
        primary = self.pick_focus(
            [t for t in live if not t.label and t.last_query], pool="unknown")
        display = knowns + ([primary] if primary else [])
        self.teachable = primary
        self.attention = self.pick_focus(display, pool="view")

        for t in display:
            if t.label and not t.sighted and t.vec is not None:
                self.memory.remember_sighting(
                    t.vec, t.label, ts=now, thumb=self._scene(frame, t.box))
                t.sighted = True
                self.log(f"seen: {t.label} ({t.score:.2f})")
        return display

    def _one_per_label(self, tracks):
        """Keep one box per label, preferring the current focus."""
        attending = self._incumbent.get("view")
        best = {}
        for t in tracks:
            key = t.label.lower()
            cur = best.get(key)
            if cur is None or t is attending:
                best[key] = t
            elif cur is not attending and (t.score, t.salience) > (cur.score,
                                                                  cur.salience):
                best[key] = t
        return list(best.values())

    def pick_focus(self, tracks, pool):
        """Choose a prominent object while keeping focus reasonably stable."""
        if not tracks:
            return None
        best = max(tracks, key=lambda t: t.salience)
        held = self._incumbent.get(pool)
        if held in tracks and best.salience < held.salience * FOCUS_MARGIN:
            best = held
        self._incumbent[pool] = best
        return best

    # -- teach ----------------------------------------------------------------

    def teach(self, crop, transcript, frame=None, box=None):
        """Store one spoken description with image and text vectors."""
        name = labels.parse_label(transcript)
        pid = self.memory.teach(
            image_vec=models.embed_crop(crop),
            text_vec=models.embed_text(transcript),
            label=name,
            transcript=transcript,
            thumb=self._thumb(crop, "taught"),
            scene=self._scene(frame, box),
        )
        self.log(f'taught "{name[:18]}" -> image + text')
        return {"id": pid, "label": name, "transcript": transcript}

    # -- forget ---------------------------------------------------------------

    def forget(self, label):
        """Delete what the robot knows about one object."""
        n = self.memory.forget(label)
        for t in getattr(self.focus, "tracks", {}).values():
            if t.label and t.label.lower() == label.lower():
                t.label = t.note = t.thumb = None
                t.requery_now()
            if t.guess and t.guess.lower() == label.lower():
                t.guess = None
                t.requery_now()
        self.log(f'forgot "{label[:18]}" -> {n} point(s)')
        return n

    # -- ask ------------------------------------------------------------------

    def ask(self, question, since_ts=None):
        """Choose an object from the question and return its latest sightings."""
        if "what did you see" in question.lower():
            since = day_start_ts() if since_ts is None else since_ts
            return {"inventory": True, "label": None, "note": None,
                    "score": 0.0, "sightings": self.memory.seen_since(since)}
        qv = models.embed_query(question)
        named = self.memory.names_in(question)
        hit = self.memory.best_taught(qv, labels=named)
        if hit is None and named:
            hit = self.memory.best_taught(qv)
        if hit is None:
            return {"inventory": False, "label": None, "note": None,
                    "score": 0.0, "sightings": []}
        label = hit.payload.get("label")
        return {"inventory": False, "label": label,
                "note": hit.payload.get("transcript"),
                "score": hit.score,
                # Teaching is also a known time and place for recall.
                "sightings": self.memory.last_sightings(
                    label, kinds=("seen", "taught"))}

    def close(self):
        self.memory.close()
