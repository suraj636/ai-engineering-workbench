# AGENTS.md

Real-time telephony voice agent: FastAPI + Plivo (telephony) + AssemblyAI streaming STT + Sarvam LLM + switchable Inworld/Speechify TTS. README.md is exhaustive (env keys, pricing, ngrok setup, API list) and CLAUDE.md repeats it in more depth (per-provider official-docs links, provider quirks) — read them before touching config or plumbing. What follows is what those make easy to miss.

## Running

- Entrypoint is `main.py` (FastAPI, all routers, static `frontend/` mount). Start with:
  `.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000`
- `PORT` in `.env` only applies to `python main.py`; the `uvicorn` CLI ignores it. Real phone calls additionally need a public HTTPS origin (`PUBLIC_BASE_URL`, e.g. ngrok) whose URL must match what Plivo was configured with; update it after a ngrok URL change and restart.
- `.env` is loaded once at import time by `backend/config.py` (`load_dotenv` of the repo-root `.env`). Changing `.env` requires a server restart (Docker: `docker compose up -d --force-recreate`).
- Startup warm-up (`backend/services/warmup.py`) opens **real paid provider sessions** in the background at every server start; status/last error at `GET /api/warmup`. Failures are reported and never fatal — a broken inactive provider doesn't affect the active one.

## Tests — critical gotchas

- The `tests/test_*.py` files are **standalone asyncio scripts, not pytest tests** and nothing in this repo installs or configures pytest. Run them directly from the repo root:
  `.venv/bin/python tests/test_web_call.py`
- They require a **running server** and connect to it via `--base-url`, which defaults to the `VOICE_AGENT_BASE_URL` env var or `http://127.0.0.1:8010` (unlike the server quick-start port 8000). Start uvicorn on 8010 or pass `--base-url` explicitly.
- They are end-to-end and hit the real paid APIs (AssemblyAI, Sarvam, active TTS) — they spend credits and take multiple minutes. There is no offline unit-test mode. One partial exception: `tests/test_outbound_call.py --smoke` checks answer XML / wiring only, no call placed.
- Each run writes a metrics JSON to `tests/results/` — generated artifacts, not fixtures; don't hand-edit or treat as source.
- Live PSTN variants exist (`tests/test_inbound_call.py --live`, `test_outbound_call.py --to +1E164NUMBER`); these require the public tunnel and place real calls.

## Runtime model

- Live state is in-memory, single-process: active calls and runtime model config reset on restart. Finished calls are also saved to SQLite (`data/calls.db`, `backend/call_db.py`) for the Cost & latency page. Never run uvicorn with multiple workers.
- Model settings can be changed at runtime via `POST /api/config` (also from the dashboard); applies to new calls only. `TTS_PROVIDER` / `TTS_MODEL` / voices / controls in `.env` are just defaults for startup.
- `python-multipart` must be installed for Plivo's form-encoded webhooks; missing it causes `/plivo/inbound` 500s even with the server running.

## Architecture notes

- Turn orchestration, streaming, barge-in and metrics all live in `backend/agents/voice_agent.py`; transports (`/ws/plivo`, `/ws/browser`) call into it. Most feature work in the call path starts there.
- TTS is pluggable via `backend/services/tts_factory.py`: a new provider must implement the existing contract — `create_context → send_text → close_context → audio` — used by both Inworld (persistent WS) and Speechify (streaming HTTP). Provider quirks an agent can break: Sarvam TTS has no cancel message, so barge-in drops its socket and reconnects in the background; Speechify allows max 2 concurrent requests process-wide (`MAX_CONCURRENT_REQUESTS` in `speechify_tts.py`).
- Audio contract: 8 kHz mono μ-law over Plivo and browser legs, 16 kHz PCM16 into AssemblyAI. Conversions are in `backend/utils/audio_utils.py` and resampling is stateful across chunks — don't instantiate it per-chunk. ASR is billed on WebSocket session time, so the STT session must be closed when the call ends.

## Tooling expectations

- There is no lint/format/typecheck/CI config in this repo — don't invoke tools it doesn't have. Python is 3.12+ (3.13 works only because `audioop-lts` in `requirements.txt` replaces the stdlib `audioop` module).
- `.gitignore` excludes `docs/`, `tests/`, `results/` and `data/` — those directories exist only locally, so never commit them and don't reference their files from committed code (a fresh clone won't have them).

## Docker deployment

`Dockerfile` + `docker-compose.yml` sit at the root: image runs `uvicorn main:app` on 8000 (HTTP only), config comes from `env_file: .env` which compose requires to exist, and `./data` is mounted so call history survives rebuilds. Same single-process rule applies — don't add workers or replicas.
