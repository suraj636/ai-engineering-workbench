# Voice agent code bundle: file guide

`voice_agent_code.zip` holds the whole project source so it can be moved through Google Drive to another laptop and pushed to a different git account.

## What is and is not in the zip

| Included | Left out on purpose |
| --- | --- |
| `backend/`, `frontend/`, `main.py`, `requirements.txt` | `.env` (live API keys; create a new one from `.env.example`) |
| `docs/`, `tests/`, `data/` | `.venv/` (rebuild it, see below) |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | `__pycache__/`, `*.pyc` (generated) |
| `.env.example`, `.gitignore` | `.git/` (history stays with the original; the new repo starts fresh or you add a remote) |
| `README.md`, `CLAUDE.md`, `AGENTS.md` | `code-zip/` itself |

`data/calls.db` is the saved call history (caller numbers, costs, possibly transcripts). It is in the zip because it was asked for. Delete it from the zip or the new laptop if you do not want that data to travel.

## Set up on the other laptop

```bash
unzip voice_agent_code.zip -d voice_websocket_prototype && cd voice_websocket_prototype
python3 -m venv .venv && source .venv/bin/activate   # Python 3.12+
pip install -r requirements.txt
cp .env.example .env                                  # fill in the keys
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 # dashboard at http://127.0.0.1:8000/
```

Push to the other account:

```bash
git init -b main
git remote add origin <new-repo-url>
git add . && git commit -m "Initial import"
git push -u origin main
```

`.gitignore` excludes `.env`, `.venv/`, `docs/`, `tests/`, `data/` and `code-zip/`, so those are not pushed. Remove a line from `.gitignore` first if you want any of them in the new repo.

## File map

### Top level

| File | Purpose |
| --- | --- |
| `main.py` | FastAPI app. Mounts `frontend/` at `/static`, serves `index.html` at `/`, includes the three routers, runs provider warm-up at startup. `python main.py` reads `PORT`; the `uvicorn` CLI does not. |
| `requirements.txt` | Dependencies: fastapi, uvicorn, websockets, httpx, python-dotenv, python-multipart (needed for Plivo form webhooks), plivo, audioop-lts (Python 3.13 only). |
| `.env.example` | Template of every setting: Plivo, AssemblyAI, Sarvam, TTS providers, agent behaviour, pricing overrides, warm-up, server. Copy to `.env`. |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | Single-process image running `uvicorn main:app` on port 8000. Never run more than one worker or replica. |
| `README.md` | Setup, env keys, pricing, ngrok, API list, metric definitions. |
| `CLAUDE.md`, `AGENTS.md` | Guidance for AI coding agents working in the repo. |
| `.gitignore` | Excludes secrets, venv, caches, docs, tests, data, logs, code-zip. |

### `backend/`

| File | Purpose |
| --- | --- |
| `config.py` | `Settings` (read once from `.env`), `RuntimeConfig` (model, TTS, voice and prompt choices, changeable for new calls via the API), the built-in system prompt, the Inworld voice catalogue. |
| `pricing.py` | Static list prices and the fixed USD to INR rate used for cost estimates. |
| `session_store.py` | In-memory call registry, `CallRecord` and `TurnMetrics`, usage and cost math, the event bus that feeds the dashboard. |
| `call_db.py` | SQLite history (`data/calls.db`). Written once when a call ends, from a worker thread. Schema version 2 with migrations. |
| `agents/voice_agent.py` | The core. `VoiceAgent` runs the state machine, turns, streaming LLM text into TTS, barge-in, playout tracking, idle and max-duration watchdog, per-turn metrics. Also defines the `Transport` protocol with `PlivoTransport` and `BrowserTransport`. |
| `routes/plivo_routes.py` | Plivo webhooks: `/plivo/inbound`, `/plivo/answer` (outbound, direction from query or Plivo field), `POST /plivo/outbound`, `/plivo/status`; signature check. |
| `routes/websocket_routes.py` | `/ws/plivo` (phone audio), `/ws/browser` (test calls), `/ws/events` (dashboard push). |
| `routes/api_routes.py` | REST API for config, stats, call history, warm-up status, network check. |
| `services/assemblyai_stt.py` | AssemblyAI streaming speech-to-text client: frames, turn events, reconnect, clean termination. |
| `services/sarvam_llm.py` | Sarvam 105B chat completions over server-sent events; captures token usage including cached tokens. |
| `services/sarvam_tts.py` | Sarvam Bulbul v4 Flash over a persistent WebSocket (`text` + `flush` per chunk, keepalive, reconnect). The default TTS. |
| `services/inworld_tts.py` | Inworld TTS-2 over a persistent WebSocket, one context per reply; manual flush mode by default. |
| `services/speechify_tts.py` | Speechify Simba over streaming HTTP, concurrent per-sentence requests played in order. |
| `services/tts_factory.py` | Chooses the TTS client from config; all clients share one interface. |
| `services/plivo_service.py` | Plivo REST client (create and hang up calls), the `<Stream>` answer XML, webhook signature verification. |
| `services/warmup.py` | Background startup warm-up of each provider; results at `/api/warmup`; never fatal. |
| `services/network_check.py` | Measures DNS, TCP, TLS and jitter to each provider for the in-app network check. |
| `utils/audio_utils.py` | μ-law and PCM conversion, `StreamResampler` (stateful, one per stream), duration helpers. |
| `utils/logging_config.py` | Pretty or JSON logging, optional transcript logging. |

### `frontend/` (plain HTML, JS, CSS; no build step)

| File | Purpose |
| --- | --- |
| `index.html` | Dashboard: live calls, web call, settings, Cost & latency page, call detail, LLM usage card. |
| `script.js` | All dashboard logic: WebSocket events, browser microphone call, charts and tables, cost rendering. |
| `style.css` | Styling. |
| `favicon.svg` | Icon. |

### `docs/` (local notes, not pushed)

| File | Content |
| --- | --- |
| `building_a_similar_voice_agent.md` | Architecture and build guide to give to a new project. |
| `scalability_and_production_readiness.md` | Concurrency limits, WebRTC vs WebSocket, LiveKit and Pipecat, hardening list. |
| `tts_chunking_explained.md` | How text chunks reach each TTS provider and why first audio differs. |
| `tts_ttfa_comparison.md` | Measured time to first audio, Sarvam vs Inworld. |
| `doc.md` | How to point the Plivo number at a server; the "number is busy" cause. |
| `network_check.md` | The network check feature. |
| `overall_flow.md` | Short flow overview (its browser audio format note may be out of date; check `script.js`). |
| `server_start_stop.md`, `frontend_security.md` | Operating and security notes. |
| `next_feature/mcp_tool_calling.md` | Plan for MCP tool calling. |
| `next_feature/cost_and_latency_dashboard.md` | Notes for the cost and latency page. |

### `tests/` (standalone asyncio scripts, not pytest)

They need a running server (default `http://127.0.0.1:8010`, or pass `--base-url`) and use the real paid APIs, except `--smoke`.

| File | Purpose |
| --- | --- |
| `test_web_call.py` | End-to-end call over `/ws/browser`. |
| `test_inbound_call.py` | Simulated Plivo inbound media over `/ws/plivo`. |
| `test_outbound_call.py` | Simulated outbound call; `--smoke` checks answer XML and wiring only. |
| `voice_test_utils.py` | Shared helpers (audio generation, scoring, reporting). |
| `results/*.json` | Output of the last runs. Generated, overwritten each run. |

### `data/`

| File | Purpose |
| --- | --- |
| `calls.db` | SQLite call history read by the Cost & latency page. Created automatically if missing. |
