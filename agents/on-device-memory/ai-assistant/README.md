# On-Device Memory Assistant

A local-first object memory demo. The camera sees an object, you name it out
loud, and the assistant stores that view. Later it recognizes the object from a
new view and answers when and where it last saw it.

Everything runs on the machine: no cloud service, no API key, and no generative
language model. The detector's own class names are ignored — the object is
called whatever you said it was.

```
camera ──▶ focus + crop ──▶ CLIP 512-d ──┐
                                         ├── Qdrant Edge ──▶ nearest taught view ≥ 0.90
mic ──▶ Whisper ──▶ "this is my mug" ────┘
                    └──▶ "my mug" + Nomic 768-d (used by ASK)
```

[Components](#components-at-a-glance) · [TEACH flow](#the-teach-flow-component-by-component) · [ASK flow](#the-ask-flow-component-by-component) · [Quick start](#run-it)

## The shape of the code

The working code is grouped by responsibility. `app.py` stays at the project
root as the entry point; everything it uses lives in the `assistant` package.

```text
minimal-assistant/
├── app.py                     # CLI entry point
├── assistant/
│   ├── config.py              # .env loading + project paths
│   ├── brain/                 # decisions, models, memory
│   │   ├── labels.py
│   │   ├── memory.py
│   │   ├── models.py
│   │   └── robot.py
│   ├── vision/                # what to look at, and how to show it
│   │   ├── draw.py
│   │   └── focus.py
│   └── web/                   # transport and UI
│       ├── page.html
│       └── server.py
├── edge-data/                 # runtime: Qdrant shard + thumbnails
├── cert/                      # runtime: generated HTTPS certificate
├── testdata/                  # replay images for a camera-free smoke test
├── yoloe-11l-seg-pf.pt        # optional detector weights (71 MB)
├── pyproject.toml
├── README.md
├── HOWTO.md
└── uv.lock
```

## Components at a glance

The very short version:

| Layer | Component | One-line job |
|---|---|---|
| Entry | `app.py` | Parses flags, builds the focus + robot, starts the live server or a headless replay. |
| Settings | `assistant/config.py` | Loads `.env`, validates every knob, and anchors the runtime paths. |
| Web | `assistant/web/server.py` | HTTP/HTTPS routes, MJPEG stream, camera + detection threads, audio intake, result cards. |
| UI | `assistant/web/page.html` | The browser page: video, TEACH/ASK/MEMORY, phone camera, microphone, spoken answers. |
| Orchestration | `assistant/brain/robot.py` | The teach/recognize/ask/forget decisions; connects focus, models and memory. |
| Models | `assistant/brain/models.py` | Lazy CLIP, Nomic and Whisper loaders. |
| Memory | `assistant/brain/memory.py` | Qdrant Edge schema, writes, image matching and text recall queries. |
| Language | `assistant/brain/labels.py` | Turns “This is my mug…” into the key `my mug`. |
| Focus | `assistant/vision/focus.py` | Chooses the subject (centre crop or YOLOE tracking) and produces its crop. |
| Overlay | `assistant/vision/draw.py` | Draws boxes, labels and the loading splash on the video. |
| Runtime | `edge-data/`, `cert/`, `testdata/`, detector weights | Persisted memories, TLS material, replay images and optional detector weights. |

A request-level view:

```text
page.html ──/state /stream /audio /frame /memory──▶ server.py
                                                     │
                              camera + detection ────┤
                                                     ▼
                                                  robot.py
                                      ┌──────────────┼──────────────┐
                                      ▼              ▼              ▼
                              vision/focus.py   brain/models.py   brain/memory.py
                                      │              │              │
                                      └──────▶ vision/draw.py       │
                                                     │              │
                                                     └──────────────┘
                                                     (labels.py is
                                                      used by teach)
```

The diagrams use basenames for width; the table above gives the full paths.

## Components in detail

### Entry and settings

#### `app.py` — the entry point

- Reads the command line: live camera or `--source` replay, shard directory,
  camera index, host/port, recognition threshold, detector limits, location,
  `--no-detector`, `--reset`.
- Builds the focus backend first, then the `Robot`, using the same code path in
  live and replay modes.
- Live mode imports `assistant.web.server.App` only when it is needed; replay
  mode never opens a web server.
- `replay()` feeds images or a video through `Robot.process_frame` and prints
  the verdict. It is a smoke test for focus, cropping, CLIP and memory.
- It contains no recognition, storage or HTTP logic.

#### `assistant/config.py` — settings and paths

- Loads `.env` from the **project root** beside `app.py`. Exported environment
  variables take precedence over the file.
- Validates values at import time, so a malformed number or crop fraction fails
  immediately with a readable message.
- Holds `RECOGNIZE_THRESHOLD` (default `0.90`), detector confidence/area limits,
  camera index/rotation/crop, `PORT`, and `WHERE`.
- `ROOT` is the project root, not the package directory. `DATA_DIR` is
  `ROOT/edge-data` and `CERT_DIR` is `ROOT/cert`, so running from another
  working directory still finds the same memory and certificate.

### Web layer

#### `assistant/web/server.py` — HTTP transport and scheduling

- `App` owns the shared state: newest camera frame, detected tracks, the MJPEG
  frame, the current result card, a monotonically increasing `card_seq`, the
  banner/status, and the `busy` flag.
- `_camera_loop` reads the laptop camera, applies `CAMERA_ROTATE` and
  `FRAME_CROP`, publishes the newest frame, and draws the overlay.
- `_detect_loop` runs about every 150 ms and calls
  `robot.process_frame(frame)` under a lock. This is where recognition and
  sighting writes happen in live mode.
- Phone frames arrive at `POST /frame`. They become the newest frame, set
  `phone_at`, and pause the laptop camera for two seconds. The phone only sends
  JPEGs; detection and CLIP still run on the computer.
- `Handler` serves `/`, `/state`, `/stream` (MJPEG), `/memory`,
  `/thumb?f=...`, `/crop.jpg`, and `/cert.crt`. It accepts
  `POST /audio?k=t|a`, `POST /forget?label=...`, and `POST /frame`.
- `on_audio` validates the action against the current `teachable` target,
  snapshots the crop/frame/box, sets `busy`, and starts a short-lived worker.
  `_voice` writes a temporary WAV, rejects silence by RMS, amplifies quiet
  phone audio, calls `models.transcribe`, then `robot.teach` or `robot.ask`.
- `ensure_cert` creates or reuses a self-signed CA certificate with SANs for
  the LAN IP, `127.0.0.1`, `localhost`, and the machine hostname. A non-loopback
  run turns on HTTPS so a phone will allow its microphone.
- The server never embeds a crop, compares a vector, or chooses a label. It
  transports, schedules, and turns results into JSON/cards.

#### `assistant/web/page.html` — the browser interface

- One page with an MJPEG `<img>`, a live focus panel (label, score bar,
  threshold marker, crop thumbnails), a status line, and result cards.
- Polls `/state` every 250 ms and refreshes `/crop.jpg` once a second.
- TEACH and ASK are tap-to-record, not hold-to-talk: tap once, speak, tap again
  to send. An `AudioWorklet` captures mono audio (16 kHz requested, with a
  device-rate fallback), encodes a WAV in the page, and posts it to
  `/audio?k=t` or `/audio?k=a`. Recording stops automatically after 30 seconds.
- `VOICE ON / OFF` speaks the answer card's `card.say` with the browser's
  `speechSynthesis`, once per new `card_seq`, and persists the choice in
  `localStorage`. The server sends text only; it does not synthesize audio
  today.
- `CAM: LAPTOP` switches to the phone's rear camera. The page asks for
  1280×960 at 4:3, draws the video to a canvas, and posts a JPEG about every
  170 ms. The feed preview follows the actual `state.frame` size with
  `object-fit: contain`, so a 4:3 phone frame is not cropped to 16:9.
- `MEMORY` fetches `/memory`, shows taught views and sightings, and offers a
  double-tap FORGET. The page has no recognition logic of its own.

### Brain layer

#### `assistant/brain/robot.py` — the decision layer

- `process_frame` asks the focus backend for stable targets, embeds each due
  crop with CLIP, and calls `memory.recognize`. The result sets `label` (known),
  `guess` (near miss, display only), `score`, `note`, and `thumb`.
- A target is re-queried at most every two seconds. A track that is already
  known and has not yet been counted gets one `seen` sighting, with a wider
  scene thumbnail, time, and location.
- It chooses `attention` (what the panel shows) and `teachable` (the salient
  unknown that TEACH will write). `pick_focus` keeps focus stable with a
  margin, and `_one_per_label` avoids showing several boxes for one name.
- `teach(crop, transcript, frame, box)` parses the spoken name, embeds the crop
  and transcript, saves thumbnails, and writes the point through `memory`.
- `ask(question)` routes to inventory or semantic recall, then returns sightings.
  It never uses the current camera frame to decide the answer.
- `forget(label)` deletes the stored points and clears matching labels from live
  tracks. `answer_line`, `stamp` and `when` build compact, deterministic text
  from stored payload fields.
- This module has no camera, HTTP, or UI imports.

#### `assistant/brain/models.py` — the three on-device models

- Lazy, lock-protected singleton loaders. Nothing heavy is imported at module
  load, so the server can show a splash while models warm.
- **CLIP ViT-B-32 vision** (`embed_crop`): 512-d image embedding. It is the
  “what is this?” side of memory.
- **Nomic Embed v1.5** (`embed_text`, `embed_query`): 768-d text embedding.
  Teach uses the document embedding; ASK uses Nomic's query prefix. This is the
  “which thing is the question about?” side.
- **Whisper base** (`transcribe`): local speech-to-text on a 16 kHz mono WAV.
  It is speech-to-text only; it does not speak. English is the default.
- `warm()` loads CLIP and Nomic at startup; Whisper loads on the first voice
  action. Model files live in `~/.cache/fastembed` unless
  `FASTEMBED_CACHE_PATH` says otherwise.

#### `assistant/brain/memory.py` — Qdrant Edge storage and queries

- One embedded shard. The schema has two named vectors of different sizes:
  `image` (512, cosine) and `text` (768, cosine). Payload indexes exist for
  `kind` and `ts`.
- `taught` points carry both vectors, the parsed label, the full transcript,
  time, place, crop thumbnail and scene thumbnail.
- `seen` points carry only the image vector plus label, time, place and a scene
  thumbnail. They are later sightings, not new taught views.
- `recognize` queries only `taught` image vectors and accepts the top hit when
  its cosine score is at or above the threshold. A score within 0.05 below the
  bar is returned as a display-only `guess`; it is never recognition and never
  creates a sighting.
- `names_in` finds taught labels that appear in the question (case-insensitive,
  simple plural handling). `best_taught` queries the text space, optionally
  restricted to those labels. `last_sightings` returns newest first, groups
  nearby writes within 600 seconds, and can include `taught` points because
  teaching also records a known time and place.
- `seen_since` returns the latest sighting of each object since a timestamp;
  `objects` builds the MEMORY panel; `forget` deletes every point for a label.
- Every write flushes the shard, so memories survive an unclean shutdown. This
  module stores and retrieves; it never decides what a crop means.

#### `assistant/brain/labels.py` — speech to an object key

- `parse_label("This is my mug, Maria made it.")` returns `my mug`.
- It looks after “this is” (and an optional article), stops at words that start
  a new clause, keeps at most five words, strips punctuation, and lowercases.
- The label is a key: forget, grouping and recall compare it. Re-teaching the
  same label adds another view rather than replacing the old one. If parsing
  produces nothing, the fallback is `unnamed`.

### Vision layer

#### `assistant/vision/focus.py` — what the camera is looking at

- `Target` is one visible thing: box, crop, stability count, last query time,
  salience, and the memory result fields (`label`, `guess`, `note`, `thumb`,
  `score`). It becomes teachable after three stable frames and is due for a
  memory query every two seconds.
- `CenterFocus` is the no-model path: it takes the middle 45% of the shorter
  frame side. It needs no torch and is the default without the detector extra.
- `YoloFocus` uses YOLOE with BoT-SORT tracking. It filters detections by
  confidence and frame area, prefers sharp/large crops, computes salience from
  size and centrality, and drops dead tracks after five seconds. It resets the
  tracker when the camera resolution changes.
- `padded_crop` adds a 12% margin and, when a segmentation mask exists, flattens
  everything outside the object to gray. That is what makes a crop survive a
  changed background or a hand holding the object.
- `build_focus` chooses YOLOE when installed, otherwise the centre crop.
  Detector weights are looked up in the project root as well as the current
  directory.
- Focus proposes boxes and crops. It never assigns a name; names come from
  memory.

#### `assistant/vision/draw.py` — the video overlay

- `draw_feed` draws one box per visible target: teal for a recognized label,
  orange for a near-miss guess, red for unknown. The focused target gets a
  thicker box and a label chip with the name and score.
- `splash` builds the still “loading models…” frame shown before the camera
  loop starts.
- It draws in OpenCV BGR and repeats the same palette used by the page's CSS.
  It is presentation only.

### Runtime files

- `edge-data/` is the shard and its `thumbs/` folder. Copy it to back up a
  memory; delete it (or use `--reset`) to start over.
- `cert/` is generated on the first non-loopback run: `cert.pem`, `key.pem`,
  and `names.txt`. It is intentionally outside the package so restarts and
  code moves keep the same certificate.
- `yoloe-11l-seg-pf.pt` is the optional detector's weights. Without the
  `detect` extra the app uses `CenterFocus`.
- `testdata/` contains replay photographs for a camera-free smoke test.

## The TEACH flow, component by component

A teach is a write, but it starts with the recognition loop that is already
running. The page does not get to name an arbitrary crop: the server only
accepts a teach when `robot.teachable` points at a focused **unknown**.

| # | Step | Component(s) | Role in the flow |
|---|---|---|---|
| 1 | A frame arrives | `server.App._camera_loop` or `page.html` → `POST /frame` | Keep the newest frame. The phone is only a camera transport; the laptop still does the seeing. |
| 2 | Focus and track | `server.App._detect_loop` → `robot.process_frame` → `focus.process` | Produce stable `Target` boxes and padded crops. YOLOE does detection/tracking; the fallback takes the centre. |
| 3 | Check whether it is already known | `robot.process_frame` → `models.embed_crop` + `memory.recognize` | CLIP’s 512-d crop is compared with taught image vectors. Sets `label`, `guess`, `score`; known objects are not teach targets. |
| 4 | Pick the teach target | `robot.pick_focus` → `robot.teachable` | The most salient unknown becomes teachable. `/state.teachable` enables the TEACH button. |
| 5 | Record the name | `page.html` | Tap TEACH, speak, tap again. The page encodes a mono WAV (16 kHz requested) and posts it to `/audio?k=t`. |
| 6 | Accept the job | `server.App.on_audio` | Under the lock, checks the teachable target, snapshots its crop/frame/box, sets `busy`, and returns 202. If nothing is teachable it returns 409. |
| 7 | Transcribe | `server._voice` → `models.transcribe` | Reject silence by RMS, amplify quiet phone audio, and run Whisper. The output is text, not speech. |
| 8 | Parse the name | `robot.teach` → `labels.parse_label` | “This is my mug.” becomes the key `my mug`. The full transcript is kept too. |
| 9 | Embed both sides | `models.embed_crop` + `models.embed_text` | CLIP produces the 512-d image vector; Nomic produces the 768-d document vector for the spoken sentence. |
| 10 | Write the memory | `memory.teach` | Upsert one `taught` point with both vectors, label, transcript, timestamp, place, thumb and scene; flush the shard. |
| 11 | Confirm | `server` card/banner + `track.requery_now` | Bump `card_seq`, show “taught: …”, and make the track query memory again on the next pass. |
| 12 | Show the result | `/state` → `page.html`; `draw.draw_feed` | The page shows the MEMORY WRITTEN card; the live box turns teal once the new view matches; the counter increases. |

```text
page.html          server.py                   robot.py              models.py       memory.py
   │ tap TEACH         │                           │                     │               │
   │── WAV ───────────▶│ on_audio ──▶ _voice       │                     │               │
   │                   │   transcribe ─────────────┼────────────────────▶│ Whisper       │
   │                   │   teach(crop, text) ─────▶│ parse_label         │               │
   │                   │                           │ embed_crop/text ───▶│ CLIP + Nomic  │
   │                   │                           │ teach ──────────────┼──────────────▶│ taught
   │◀── /state ────────│◀── card + banner ─────────│                     │               │
```

Once the re-query matches, the **next detection pass** may add one `seen`
sighting for that track. That write belongs to the recognition loop, not to
`robot.teach` itself.

Not used by teach: `memory.names_in`, `memory.best_taught`,
`memory.last_sightings`, and `memory.seen_since`. Those are recall queries.
`draw.py` only shows feedback; it does not affect what is stored.

## The ASK flow, component by component

ASK is a read. It does **not** look at the current camera frame to answer.
By the time you ask, the recognition loop has already been writing sightings
for objects it recognizes. ASK turns your question into a text query and reads
the stored payloads.

| # | Step | Component(s) | Role in the flow |
|---|---|---|---|
| 1 | Keep the context current | `focus` → `robot.process_frame` → `models.embed_crop` → `memory.recognize` / `remember_sighting` | While the app runs, known objects get `seen` points with their scene, time and place. This is what “last seen” refers to. |
| 2 | Record the question | `page.html` | Tap ASK, speak, tap again. Same mono WAV path, posted to `/audio?k=a`. |
| 3 | Accept and transcribe | `server.App.on_audio` → `server._voice` → `models.transcribe` | Reuse the teach audio path, then hand the transcript to `robot.ask`. |
| 4 | Route the question | `robot.ask` | Exact substring `what did you see` selects the inventory branch; everything else uses semantic recall. |
| 5 | Inventory branch (optional) | `memory.seen_since(day_start_ts())` | Return the latest `seen` point per object since midnight, newest first, at most four. |
| 6 | Embed the question | `models.embed_query` | Nomic’s **query** embedding, not the document embedding used at teach time. |
| 7 | Find explicit names | `memory.names_in(question)` | Match taught labels that literally occur in the question. This lets a named object win over a semantically similar one. |
| 8 | Choose the object | `memory.best_taught(qv, labels=named)` | Nearest taught transcript in the text space. If a named filter finds nothing, it retries without the filter. There is no acceptance threshold here. |
| 9 | Read the sightings | `memory.last_sightings(label, kinds=("seen", "taught"))` | Newest first, at most three, grouping writes within ten minutes. A teach point counts because it records a known time and place. |
| 10 | Build the sentence | `robot.answer_line` | Use only stored payload fields: label, timestamp, place. No model writes the answer. |
| 11 | Build the card | `server._card_json` | Attach `say`, plus each hit’s label, compact time, place, kind (`seen`/`taught`) and thumbnail. |
| 12 | Show and speak | `page.html` | Draw the RECALL card and speak its `card.say` with browser `speechSynthesis`, once per new `card_seq`. |

```text
page.html          server.py                   robot.py              models.py       memory.py
   │ tap ASK          │                           │                     │               │
   │── WAV ──────────▶│ on_audio ──▶ _voice       │                     │               │
   │                  │   transcribe ─────────────┼────────────────────▶│ Whisper       │
   │                  │   ask(question) ─────────▶│ embed_query ───────▶│ Nomic query   │
   │                  │                           │ names_in ───────────┼──────────────▶│ exact labels
   │                  │                           │ best_taught ────────┼──────────────▶│ text nearest
   │                  │                           │ last_sightings ─────┼──────────────▶│ payloads
   │◀── /state ───────│◀── answer card + banner ──│                     │               │
   │ speak(card.say)  │                           │                     │               │
```

What the answer sounds like:

- A normal hit: `my mug: 2:45 PM at desk`
- Known, but no sightings: `I know my mug, but have not seen it`
- No taught object matched: `I don't know that one yet`
- Inventory: `today: my mug, keys` (or `nothing seen today`)

The spoken time comes from `when()`: today’s sightings are a clock time; older
ones include the month and day. The card itself uses `stamp()` for a compact
24-hour form (for example `Oct 21, 14:38`). Both are built from Qdrant payload
fields; the text is never generated by an LLM.

ASK itself does not write memory. The recognition loop may have written a
`seen` point moments earlier; ASK only reads it. `forget` is separate and does
write (deletes).

## TEACH vs ASK: the same components, different jobs

| Component | In TEACH | In ASK |
|---|---|---|
| `app.py` | Starts the app and constructs the focus + robot. | Same; no per-question work. |
| `assistant/config.py` | Supplies threshold, paths, `WHERE`. | Same threshold and paths; `WHERE` comes from stored payloads. |
| `assistant/web/server.py` | Accepts `/audio?k=t`, snapshots the teach target, runs the voice job, calls `robot.teach`, makes the taught card. | Accepts `/audio?k=a`, runs the voice job, calls `robot.ask`, makes the answer card. |
| `assistant/web/page.html` | Captures the spoken name, shows the taught card, renders the teal box. | Captures the question, shows the RECALL card, speaks the answer. |
| `assistant/brain/robot.py` | `teach()` parses, embeds and writes a taught view. | `process_frame()` keeps sightings current; `ask()` selects a label and reads sightings. |
| `assistant/brain/models.py` | CLIP image embedding + Nomic document embedding + Whisper. | CLIP in the recognition loop + Nomic query embedding + Whisper. |
| `assistant/brain/memory.py` | `teach()` upserts `kind=taught` with both vectors. | `recognize`, `names_in`, `best_taught`, `last_sightings`, `seen_since`; sightings written by the loop. |
| `assistant/brain/labels.py` | `parse_label()` turns the sentence into the stored key. | Not called; the labels stored at teach time are queried. |
| `assistant/vision/focus.py` | Selects and crops the object to teach. | Keeps selecting the current view for the recognition loop; it does not answer the question. |
| `assistant/vision/draw.py` | Draws the live box and taught-name feedback. | Draws the live box and recognized name; the answer text comes from memory. |

The rule to keep in mind:

- **Image space** answers “what is this?” — CLIP, nearest `taught` image,
  threshold `0.90`.
- **Text space** answers “which thing is the question about?” — Nomic, nearest
  taught transcript, no acceptance threshold.
- **Payload** answers “when and where?” — timestamps, places and thumbnails
  stored on the point. No language model is involved.

## What is stored

One Qdrant Edge shard in `edge-data/`:

| Vector | Size | Model | Used for |
|---|---|---|---|
| `image` | 512 | CLIP ViT-B-32 vision | “What is this?” |
| `text` | 768 | Nomic Embed v1.5 | “Which taught thing is the question about?” |

| Kind | Vectors | Meaning |
|---|---|---|
| `taught` | `image` + `text` | A named example view and the spoken sentence. |
| `seen` | `image` | A later sighting of a known object. |

Scores just below `RECOGNIZE_THRESHOLD` are shown as guesses (within 0.05) but
never become recognition. Labels are keys: `labels.parse_label` lowercases and
trims them, and forget/recall compare exact strings. Teaching the same label
again adds another view rather than replacing one.

## Run it

Run commands from the project root (the folder containing `app.py`). You need
Python 3.12+, [uv](https://docs.astral.sh/uv/), a webcam, and a microphone.

```bash
uv sync --extra detect      # reference behaviour: YOLOE tracking
cp .env.example .env
uv run python app.py
```

Prefer no torch? `uv sync` alone installs the core and the app uses the centre
crop instead; every other feature is identical. Note that a plain `uv sync`
also removes the detector packages again if they are already installed.

Open <http://127.0.0.1:8765>. The first run downloads about 1.1 GB of model
files into `~/.cache/fastembed`; Whisper is fetched on the first voice action.

Hold an object in the middle of the frame, tap **TEACH**, and say
*“This is my mug.”* Tap **TEACH** again to send. Move the object away, then
bring it back: the box turns teal with the name on it. Tap **ASK**, say
*“When did you last see my mug?”*, and tap again. The answer is shown on a card
and read aloud by the browser tab that asked; tap **VOICE ON** to mute it.

Teach the same object two or three times from different angles. Each teach adds
a view; recognition searches all of them.

The full step-by-step runbook, including tuning and recovery, is in
[`HOWTO.md`](HOWTO.md).

### Controls

| Control | Action |
|---|---|
| **TEACH** | Tap, speak, tap again to send. Teaches the focused unknown object. |
| **ASK** | Tap, ask, tap again to send. Returns when and where it was seen. |
| **MEMORY** | Browse what it knows, with pictures, and forget an object. |
| **VOICE ON / OFF** | Speak the answer aloud in this browser tab. |
| **CAM: LAPTOP / CAM: PHONE** | Switch between the computer's camera and the phone's. |
| Ctrl-C | Stop. |

### Use it from your phone

The phone is the screen and the microphone; the camera is the computer's by
default. Browsers only allow the microphone on a secure origin, so a run that
listens on the network generates a local certificate and serves **HTTPS**
automatically:

```bash
uv run python app.py --host 0.0.0.0
```

It prints something like `https://192.168.1.94:8765`. On the phone, connected to
the same Wi-Fi, open that address, accept the certificate warning
(**Advanced → Proceed**), and allow the microphone. If the detected address is
wrong, pass `--advertise 192.168.1.94`.

To stop the warning after every restart, install the certificate once:
open `https://<your-ip>:8765/cert.crt` on the phone and follow
[iOS](https://support.apple.com/en-us/102390) or Android's *Install a CA
certificate* flow.

Linux firewall blocking the port? `sudo ufw allow 8765/tcp`.

Tap **CAM: LAPTOP** under the buttons and allow the camera; it becomes
**CAM: PHONE** and the feed switches to the phone's rear camera. The phone only
posts JPEG frames (about six a second); detection, CLIP, and the memory still
run on the computer. The requested shape is 4:3, and the live view follows the
actual frame size, so you see more of the scene vertically. Tap again to return
to the webcam. The switch takes a second or two because the app waits for the
phone's frames to stop before releasing the laptop camera.

Only toggle it on one phone at a time — the newest frames win, so two phones
would fight over the feed.

The page has **no login**. Anyone on the network who opens the URL can teach,
browse, and forget memories, so use it on a trusted network and stop the app
with Ctrl-C when you are done.

### Optional: automatic detection

Without the detector extra, the assistant uses the **centre crop**: whatever
fills the middle 45% of the frame is the subject. That is enough for the demo
and avoids a large torch install.

For YOLOE detection, segmentation, BoT-SORT tracking and salience-based focus:

```bash
uv sync --extra detect
uv run python app.py
```

The first run also downloads `yoloe-11l-seg-pf.pt` into the project directory.
`--no-detector` forces the centre crop even when YOLOE is installed.

## Troubleshooting

**Everything matches the thing I taught.** Your threshold is too low. Try
`--threshold 0.92`, or re-teach with two more angles.

**No box appears at all.** With the detector, `DETECT_MAX_AREA` may be
filtering your object out: the default keeps proposals below 20% of the frame.
Close-up objects are bigger than that. Try `--max-area 0.5` (the bundled vase
photos measure 35–38%, so the replay needs this).

**It does not recognize the object from a new angle.** Teach that angle. One
view is one point in CLIP space.

**The camera does not open.** Close video calls and other tabs using it, then
try `--camera 1`.

**“didn't hear anything”.** The browser's mic is the input, so check the site
permission for the address in the URL bar. On a phone, use the `https://` URL
the app prints (it generates the certificate), not a hand-typed `http://` LAN
address, which browsers refuse to give the microphone. On the laptop,
`http://127.0.0.1:8765` is already a secure origin.

**The transcript is wrong or empty on the phone.** Phone microphones are quiet
when held at arm's length, so recordings below normal speech level are
amplified (up to 15×) before Whisper. Speak toward the phone and don't cover
the mic, usually at the bottom edge. The terminal prints `recorded level (rms)`
and the gain applied — speech is typically 500–3000 before amplification.

**Start over.** `uv run python app.py --reset`.

**Check the pipeline without a camera.**

```bash
uv run python app.py --data /tmp/memory-smoke --reset --source testdata/
```

With an empty memory the images print as `UNKNOWN`; that is a smoke test, not
an accuracy test. The temporary shard keeps your real `edge-data/` untouched.
The bundled photographs come from Wikimedia Commons under CC BY-SA 4.0;
`testdata/CREDITS.json` names each photographer.

The reference project this demo is trimmed from:
<https://github.com/https-deeplearning-ai/sc-on-device-ai-memory>.
