# Complete How-To

Everything from a clean checkout to a working demo, plus tuning and recovery.
Commands assume you are in the project folder:

```bash
cd "/home/webexpert/Desktop/Eminence/Demo Projects/on device ai assistant/minimal-assistant"
```

The source is grouped under `assistant/` (`brain/`, `vision/`, `web/`);
`app.py` remains the entry point. See `README.md` for the component briefing
and the TEACH/ASK flow.

---

## 1. What you need

- Linux, macOS, or 64-bit Windows.
- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/) installed.
- A webcam and a microphone.
- ~2 GB of disk for packages, ~1.1 GB for models, 71 MB for the detector.
- Internet on the first run only. After that it is fully offline.

Check the basics:

```bash
python3 --version     # 3.12+
uv --version
```

---

## 2. Install

Full setup, with automatic object detection (recommended):

```bash
uv sync --extra detect
cp .env.example .env
```

Without the detector — no torch, ~430 MB instead of ~6 GB, uses a centre crop:

```bash
uv sync
cp .env.example .env
```

> A plain `uv sync` also uninstalls torch if it is already there. Always use
> `uv sync --extra detect` once you have chosen the detector.

The first launch downloads CLIP (~350 MB), Nomic (~550 MB), and Whisper
(~150 MB) to `~/.cache/fastembed`, and `yoloe-11l-seg-pf.pt` (71 MB) into the
project folder. This happens once.

---

## 3. Start it

```bash
uv run python app.py
```

You should see:

```
focus: YOLOE tracking (cuda)
live view: http://127.0.0.1:8765   (Ctrl-C stops the assistant)
loading models...
loading CLIP vision encoder...
loading Nomic text encoder...
loading YOLOE detector...
ready
```

A browser opens <http://127.0.0.1:8765>. If it does not, open that address
manually. Allow the microphone when the browser asks.

On a phone, use the `https://` address the app prints when started with
`--host 0.0.0.0` — see section 5. Don't type an `http://` LAN address by hand:
browsers deny the microphone on an insecure origin.

Stop with **Ctrl-C** in the terminal.

---

## 4. The demo, step by step

### Teach an object

1. Hold the object in the middle of the frame, about arm's length away.
2. Wait for a red box labelled `UNKNOWN`. This is the teach target. The
   thickest box is the focused one.
3. **Tap TEACH.** It turns red and starts recording.
4. Say clearly: **"This is my mug."**
5. **Tap TEACH again** to send. The status line shows `thinking...`, then
   `taught: "my mug"`.

There is no need to keep a finger on the button — recording continues until you
tap it again, or until 30 seconds pass.

The object turns **teal** with its new name and a similarity score.

### Teach it from more angles

Turn the object and repeat: tap **TEACH**, say *"This is my mug"*, tap again.
Do this 2–3 times. Each teach adds a view to the shard; it does not replace the
previous one. More angles means more reliable recognition.

### Recognize it

Move the object out of view, then bring it back. Within about two seconds the
box turns teal and shows the name.

### Ask about it

1. Tap **ASK**.
2. Ask: **"When did you last see my mug?"**
3. Tap **ASK** again. The recall card shows the time, the place, and the stored
   photo. The answer is spoken aloud in the tab that asked; **VOICE ON** in the
   panel header mutes it.

Other useful questions:

- "Where did you last see my mug?"
- "What did you see today?" — lists everything sighted since midnight.

Recall matches the question against the sentences you spoke while teaching, so
natural sentences work better than single nouns.

### Browse and delete

Press **MEMORY**. You get every taught object with its photo, the number of
views and sightings, and a **FORGET** button (tap twice to confirm). Close
returns to the live feed.

### A good demo run (90 seconds)

1. Show a mug, teach it three times while rotating it.
2. Hide it, reveal it → teal box, correct name.
3. Show a different object, teach it once, hide it.
4. Show the mug again → it is still the mug, not the newer object.
5. ASK "when did you last see my mug" → time and place.
6. MEMORY → show the stored views and sightings.
7. Ctrl-C, restart, show the mug → it still remembers. That is the
   point: the memory is on disk, not in the process.

---

## 5. Use it from your phone

The phone is the screen and the microphone; the camera is the computer's unless
you switch it. Browsers only allow the microphone over HTTPS, so a non-loopback
run automatically generates a certificate and serves HTTPS. Nothing else
changes.

1. Put the phone and the computer on the **same Wi-Fi**.
2. Start the assistant on the network:

   ```bash
   uv run python app.py --host 0.0.0.0
   ```

3. Read the printed address, e.g. `https://192.168.1.94:8765`. If the detected
   address is wrong, name it yourself:

   ```bash
   uv run python app.py --host 0.0.0.0 --advertise 192.168.1.94
   ```

4. Open that address on the phone.
5. The browser warns about the certificate. Choose **Advanced → Proceed**.
6. Allow the microphone.

In landscape the page becomes video + side panel; in portrait the buttons sit
under the video.

### Trust the certificate permanently (optional)

Without this you re-accept the warning after every restart. With it, the phone
treats the assistant as a normal secure site.

1. On the phone, open `https://<your-ip>:8765/cert.crt` and download it.
2. **iPhone / iPad**: Settings → Profile Downloaded → Install. Then Settings →
   General → About → Certificate Trust Settings → enable full trust for
   *on-device-memory-assistant*.
3. **Android**: Settings → Security → Encryption & credentials → Install a
   certificate → **CA certificate** → allow and pick the downloaded file.

### Use the phone's camera

The feed is the computer's webcam by default. Tap **CAM: LAPTOP**, allow the
camera, and the button becomes **CAM: PHONE**: the feed now comes from the
phone's rear camera.

The heavy work stays on the computer. The phone posts a JPEG about six times a
second, the laptop detects, embeds, and matches, and the annotated feed comes
back to the phone. Recognition speed is unchanged; expect a fraction of a second
of extra lag and a little more Wi-Fi traffic. The phone camera is asked for 4:3
and the live view takes that shape, so the bottom of the scene is not cropped
away the way it is in a 16:9 feed.

Tap **CAM: PHONE** to go back. The laptop camera takes over after about two
seconds, because the app waits for the phone's frames to stop before releasing
it. Use the toggle on one phone at a time.

### If the phone cannot connect

- Same network? Guest Wi-Fi often blocks device-to-device traffic.
- Linux firewall: `sudo ufw allow 8765/tcp`.
- Wrong interface: check `ip addr` and pass `--advertise <that-ip>`.
- A VPN on the computer can pull LAN traffic away; disconnect it.

### TEACH and ASK on the phone

Tap the on-screen button, speak, tap it again — identical to the laptop. You do
not have to keep your finger down:

- **TEACH**: "This is my mug."
- **ASK**: "When did you last see my mug?"
- **MEMORY**: review, then FORGET (tap twice).
- **CAM: LAPTOP / CAM: PHONE**: swap the camera.
- **VOICE ON / OFF**: speak answers aloud in this tab.

Speak toward the phone: its microphone is usually at the bottom edge. Quiet
recordings are amplified automatically, but distance and background noise still
make transcription worse.

There is no on-screen quit; a stray tap cannot end the demo.

### Security

The page has **no login**. Anyone on the network who opens the URL can teach,
browse and forget memories. Use it on a trusted network and stop the app with
Ctrl-C when the demo ends.

## 6. Tuning

All settings live in `.env` (copy of `.env.example`), and several have a
command-line override for a single run.

| Setting | Default | What it does |
|---|---|---|
| `RECOGNIZE_THRESHOLD` | `0.90` | Cosine similarity needed to accept a match. |
| `DETECT_CONF` | `0.30` | Detector confidence floor. Raise to track less clutter. |
| `DETECT_MAX_AREA` | `0.20` | Biggest proposal kept, as a fraction of the frame. |
| `DETECT_MIN_AREA` | `0.001` | Smallest proposal kept. |
| `CAMERA_INDEX` | `0` | Webcam index. |
| `CAMERA_ROTATE` | `0` | `180` for an upside-down camera. |
| `FRAME_CROP` | `0` | Trim a lens rim: one fraction or `left,top,right,bottom`. |
| `PORT` | `8765` | Web server port. |
| `WHERE` | unset | Place stamped on new memories, e.g. `desk`. |

### If nothing gets a box

Your object is probably larger than 20% of the frame. Either raise the limit
for the run:

```bash
uv run python app.py --max-area 0.5
```

or set `DETECT_MAX_AREA=0.5` in `.env`. The bundled vase photos measure 35–38%,
so `--max-area 0.5` is needed for the image replay.

### If it matches the wrong object

The threshold is too low for your camera. Try `--threshold 0.92`, or re-teach
with clearer views. Unrelated same-camera objects typically score 0.75–0.85, so
0.90 is a real bar, not a confidence percentage.

### If it does not recognize a new angle

Teach that angle. One taught view is one point in CLIP space; a turned object is
a different point.

### If recognition is slow

Detection on CPU is the bottleneck. Options: run with `--no-detector` (centre
crop, much faster), raise `DETECT_CONF`, or use a CUDA GPU (detected
automatically).

---

## 7. Command reference

```bash
uv run python app.py [options]
```

| Flag | Default | Meaning |
|---|---|---|
| `--source DIR\|FILE` | live camera | Replay a folder of images or a video, headless. |
| `--data DIR` | `edge-data` | Shard directory. |
| `--camera N` | `0` | Camera index. |
| `--host ADDR` | `127.0.0.1` | Bind address. Use `0.0.0.0` to serve a phone; that switches on HTTPS. |
| `--advertise ADDR` | detected | IP address to print and put in the certificate, if detection picks the wrong one. |
| `--port N` | `8765` | Port. |
| `--threshold F` | `0.90` | Recognition threshold for this run. |
| `--conf F` | `0.30` | Detector confidence floor. |
| `--max-area F` | `0.20` | Biggest detection kept. |
| `--location TEXT` | `WHERE` | Place stamped on new memories. |
| `--no-detector` | off | Force the centre crop even if YOLOE is installed. |
| `--reset` | off | Delete the shard before starting. |

Examples:

```bash
uv run python app.py --camera 1
uv run python app.py --max-area 0.5 --threshold 0.88
uv run python app.py --port 8766
uv run python app.py --location "demo table"
uv run python app.py --reset                 # clean slate
uv run python app.py --source testdata/      # no camera needed
uv run python app.py --source clip.mp4       # replay a recording
uv run python app.py --host 0.0.0.0          # phone over HTTPS
uv run python app.py --host 0.0.0.0 --advertise 192.168.1.94
```

---

## 8. Where things live

| Path | Contents |
|---|---|
| `edge-data/` | Qdrant Edge shard, taught and seen points. |
| `edge-data/thumbs/` | The pictures shown in the memory panel. |
| `cert/` | Generated certificate and key, created on the first LAN run. |
| `~/.cache/fastembed/` | CLIP, Nomic, and Whisper model files. |
| `yoloe-11l-seg-pf.pt` | Detector weights, 71 MB. |

Back up a memory by copying `edge-data/`. Start over with `--reset` or by
deleting the folder.

The schema, for when you want to inspect or extend it:

| Field | Meaning |
|---|---|
| `kind` | `taught` (image + text vectors) or `seen` (image only). |
| `label` | The name parsed from your sentence, lowercased. |
| `transcript` | The full sentence you spoke. |
| `ts`, `where` | When and where. |
| `thumb`, `scene` | Recognition crop and the wider scene picture. |

---

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `camera 0 did not open` | Another app holds the camera. Close video calls/browser tabs, try `--camera 1`. |
| Page loads, feed is black | Camera is starting or blocked. Check the terminal, then refresh. |
| `nothing new in focus to teach` | No unknown object is tracked. Move it to the centre, wait for a red box, or raise `--max-area`. |
| `didn't hear anything` | Mic permission denied, or you tapped stop before speaking. Tap once, speak, then tap again. |
| Transcript is wrong | Speak a full sentence; Whisper needs context. Move closer, reduce background noise. Quiet phone audio is amplified automatically; check `recorded level (rms)` in the terminal (speech is ~500–3000). |
| TEACH does nothing on a phone | You opened an `http://` LAN address. Use the `https://` URL the app prints and accept the certificate warning. |
| Everything matches one object | Threshold too low. `--threshold 0.92`, and teach more views. |
| Object never recognized | Threshold too high or angle unseen. Teach the angle, or try `--threshold 0.88`. |
| `focus: centre crop` but you installed the detector | Run `uv sync --extra detect` again. |
| Port already in use | `--port 8766`. |
| Models re-download every run | `~/.cache/fastembed` is being cleared. Set `FASTEMBED_CACHE_PATH` to a durable folder. |
| Want a clean slate | `uv run python app.py --reset`. |

Logs appear in the terminal, not the browser. If a voice action fails, the
reason is printed there.

---

## 10. Run without a camera

Useful for checking an install or demonstrating the memory layer:

```bash
uv run python app.py --data /tmp/memory-smoke --reset --source testdata/
```

With the empty temporary shard every image prints `UNKNOWN`, and your real
`edge-data/` is untouched. That is a smoke test of detection, cropping, CLIP,
and the shard query, not an accuracy test.

To teach during a replay, use the Python API:

```python
import cv2
from assistant.brain.robot import Robot, answer_line
from assistant.vision.focus import YoloFocus

r = Robot(data_dir="edge-data", focus=YoloFocus(max_area=0.5), where="desk")
img = cv2.imread("testdata/vase_1.jpg")
for i in range(5):
    r.process_frame(img, 1000 + i * 0.2)
r.teach(r.attention.crop, "This is my vase", frame=img, box=r.attention.box)
print(answer_line(r.ask("when did you last see my vase")))
r.close()
```

---

## 11. How it works in 30 seconds

```
camera → detect + track → 12% padded, mask-flattened crop → CLIP 512-d
                                                                    │
voice → Whisper → "this is my mug" → "my mug" → Nomic 768-d         │
                                                                    ▼
                                            Qdrant Edge shard, cosine search
                                                                    │
                              nearest taught image ≥ 0.90 → recognized
                              nearest taught text → which object the question is about
```

Two named vectors of different sizes live in one shard: `image` (512, CLIP) for
"what is this?" and `text` (768, Nomic) for "which thing am I being asked
about?". Sightings store the image vector only. Answers come from payload
fields — no language model writes them.

The reference project this is trimmed from:
<https://github.com/https-deeplearning-ai/sc-on-device-ai-memory>
