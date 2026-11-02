"""The memory: one Qdrant Edge shard, two named vectors, one threshold.

    taught  a view someone named out loud. Both image and text vectors.
    seen    a later sighting of a known object. Image vector only.

The image space answers "what is this?"; the text space answers "which of the
things I know is the question about?". They never mix.
"""
import itertools
import re
import threading
import time
from pathlib import Path

from qdrant_edge import (
    CountRequest,
    Distance,
    EdgeConfig,
    EdgeShard,
    EdgeVectorParams,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    Point,
    Query,
    QueryRequest,
    RangeFloat,
    ScrollRequest,
    UpdateOperation,
)

from ..config import RECOGNIZE_THRESHOLD

# Scores just below the bar are shown as guesses. They never count as
# recognition and never create a sighting.
MAYBE_MARGIN = 0.05

# Group nearby sightings into one occasion for display and deletion.
SIGHTING_APART = 600

CONFIG = EdgeConfig(vectors={
    "text": EdgeVectorParams(size=768, distance=Distance.Cosine),
    "image": EdgeVectorParams(size=512, distance=Distance.Cosine),
})


class Memory:
    """Store taught views, recognize objects, and recall their sightings."""

    def __init__(self, data_dir, threshold=RECOGNIZE_THRESHOLD, where=None):
        self.threshold = threshold
        self.dir = Path(data_dir)
        # Compound reads call other locked methods, so this must be an RLock.
        # Two threads touch the shard: the camera loop and a voice action.
        self._lock = threading.RLock()
        self.where = where
        # Load an existing shard, but ignore the files this app stores beside
        # it when deciding whether one exists.
        others = [p for p in self.dir.glob("*")
                  if p.name not in ("thumbs", "where.txt")]
        if (self.dir / "segments").exists() or others:
            self.shard = EdgeShard.load(str(self.dir))
        else:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.shard = EdgeShard.create(str(self.dir), CONFIG)
            for key, schema in [
                ("kind", PayloadSchemaType.Keyword),
                ("ts", PayloadSchemaType.Float),
            ]:
                self.shard.update(
                    UpdateOperation.create_field_index(key, schema))
        # Clock-based IDs avoid a shard scan. Kept as ints here; the page
        # receives strings because they exceed JavaScript's safe range.
        self._ids = itertools.count(time.time_ns())

    def close(self):
        with self._lock:
            self.shard.close()

    def count(self):
        with self._lock:
            return self.shard.count(CountRequest(exact=True))

    # -- writes ---------------------------------------------------------------

    def _upsert(self, vector, payload):
        with self._lock:
            pid = next(self._ids)
            self.shard.update(UpdateOperation.upsert_points([
                Point(id=pid, vector=vector, payload=payload)
            ]))
            # The appliance may lose power without a clean shutdown.
            self.shard.flush()
            return pid

    def teach(self, image_vec, text_vec, label, transcript, ts=None, thumb=None,
              scene=None):
        """Store a view with both vectors. Re-teaching adds another view, so
        recognition can match more angles instead of replacing one."""
        return self._upsert(
            {"image": image_vec, "text": text_vec},
            {
                "kind": "taught",
                "label": label,
                "transcript": transcript,
                "ts": ts or time.time(),
                "thumb": thumb,
                "scene": scene,
                "where": self.where,
            },
        )

    def remember_sighting(self, image_vec, label, ts=None, thumb=None):
        """Something known is in view. Image vector only."""
        return self._upsert(
            {"image": image_vec},
            {
                "kind": "seen",
                "label": label,
                "ts": ts or time.time(),
                "thumb": thumb,
                "where": self.where,
            },
        )

    def forget(self, label):
        """Delete every taught view and sighting for a label."""
        with self._lock:
            records, _ = self.shard.scroll(
                ScrollRequest(limit=10000, with_payload=True))
            ids = [p.id for p in records
                   if (p.payload.get("label") or "").lower() == label.lower()]
            if ids:
                self.shard.update(UpdateOperation.delete_points(ids))
                self.shard.flush()
            return len(ids)

    # -- reads ----------------------------------------------------------------

    def count_sightings(self, label):
        with self._lock:
            return self.shard.count(CountRequest(
                exact=True, filter=Filter(must=[
                    FieldCondition(key="kind", match=MatchValue(value="seen")),
                    FieldCondition(key="label", match=MatchValue(value=label)),
                ])))

    def objects(self):
        """Taught objects newest first, grouped case-insensitively."""
        with self._lock:
            records, _ = self.shard.scroll(ScrollRequest(
                limit=10000, with_payload=True,
                filter=Filter(must=[
                    FieldCondition(key="kind",
                                   match=MatchValue(value="taught")),
                ])))
            groups = {}
            for r in records:
                label = r.payload.get("label") or ""
                g = groups.setdefault(label.lower(),
                                      {"label": label, "views": []})
                g["views"].append(dict(r.payload, id=r.id))
            out = []
            for g in groups.values():
                g["views"].sort(key=lambda p: p.get("ts") or 0, reverse=True)
                g["label"] = g["views"][0].get("label") or g["label"]
                g["seen"] = self.count_sightings(g["label"])
                g["sightings"] = [
                    dict(r.payload, id=r.id)
                    for r in self.last_sightings(g["label"], limit=None)]
                out.append(g)
            out.sort(key=lambda g: g["views"][0].get("ts") or 0, reverse=True)
            return out

    def recognize(self, image_vec):
        """Nearest taught view against the threshold. This is the whole rule.

        Returns (hit|None, score, guess). `guess` is the nearest label when the
        score lands just under the bar, for display only.
        """
        with self._lock:
            hits = self.shard.query(QueryRequest(
                query=Query.Nearest(image_vec, using="image"),
                filter=Filter(must=[
                    FieldCondition(key="kind",
                                   match=MatchValue(value="taught")),
                ]),
                limit=1,
                with_payload=True,
            ))
        if not hits:
            return None, 0.0, None
        top = hits[0]
        if top.score >= self.threshold:
            return top, top.score, None
        if top.score >= self.threshold - MAYBE_MARGIN:
            return None, top.score, top.payload.get("label")
        return None, top.score, None

    def names_in(self, question):
        """Taught labels that appear as words in a question.

        Matching ignores case and a trailing plural "s" on longer words. One
        token labels shorter than three characters are skipped to avoid
        transcription noise. The caller uses matches to narrow semantic search.
        """
        def toks(text):
            out = []
            for w in re.split(r"[^0-9a-z]+", text.lower()):
                if w:
                    out.append(w[:-1] if len(w) >= 4 and w.endswith("s") else w)
            return out

        asked = toks(question)
        with self._lock:
            records, _ = self.shard.scroll(ScrollRequest(
                limit=10000, with_payload=True,
                filter=Filter(must=[
                    FieldCondition(key="kind",
                                   match=MatchValue(value="taught")),
                ])))
        hits = {}
        for r in records:
            label = r.payload.get("label") or ""
            want = toks(label)
            if not want or (len(want) == 1 and len(want[0]) < 3):
                continue
            if any(asked[i:i + len(want)] == want
                   for i in range(len(asked) - len(want) + 1)):
                ts = r.payload.get("ts") or 0
                hits[label] = max(ts, hits.get(label, 0))
        return [label for label, _ in sorted(hits.items(), key=lambda kv: -kv[1])]

    def best_taught(self, text_vec, labels=None):
        """The taught transcript closest to a question vector.

        When `labels` is nonempty only those explicitly named objects are
        candidates, so a semantically similar name cannot replace an explicit
        request. The score is shown but has no acceptance threshold.
        """
        must = [FieldCondition(key="kind", match=MatchValue(value="taught"))]
        if labels:
            must.append(
                FieldCondition(key="label", match=MatchAny(list(labels))))
        with self._lock:
            hits = self.shard.query(QueryRequest(
                query=Query.Nearest(text_vec, using="text"),
                filter=Filter(must=must),
                limit=1,
                with_payload=True,
            ))
        return hits[0] if hits else None

    def last_sightings(self, label, limit=3, kinds=("seen",)):
        """Newest sightings, grouping nearby writes into one occasion.

        Recall includes taught points because teaching also records a known
        time and place. The memory panel asks for sightings only.
        """
        with self._lock:
            records, _ = self.shard.scroll(ScrollRequest(
                limit=10000, with_payload=True,
                filter=Filter(must=[
                    FieldCondition(key="kind", match=MatchAny(list(kinds))),
                ])))
        rows = [r for r in records
                if (r.payload.get("label") or "").lower() == label.lower()]
        rows.sort(key=lambda r: r.payload.get("ts") or 0, reverse=True)
        out = []
        for r in rows:
            if out and ((out[-1].payload.get("ts") or 0)
                        - (r.payload.get("ts") or 0)) < SIGHTING_APART:
                continue
            out.append(r)
            if limit is not None and len(out) >= limit:
                break
        return out

    def seen_since(self, since_ts, limit=4):
        """Latest sighting of each object since a timestamp."""
        with self._lock:
            records, _ = self.shard.scroll(ScrollRequest(
                limit=10000, with_payload=True,
                filter=Filter(must=[
                    FieldCondition(key="kind",
                                   match=MatchValue(value="seen")),
                    FieldCondition(key="ts", range=RangeFloat(gte=since_ts)),
                ])))
        records.sort(key=lambda r: r.payload.get("ts") or 0, reverse=True)
        out, seen = [], set()
        for r in records:
            key = (r.payload.get("label") or "").lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
            if len(out) >= limit:
                break
        return out
