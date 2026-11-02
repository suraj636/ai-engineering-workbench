# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Real-time telephony voice agent: FastAPI + Plivo + AssemblyAI streaming STT + Sarvam 105B LLM + switchable Inworld / Speechify TTS. `README.md` is exhaustive (env keys, pricing, ngrok setup, API list, metric definitions) and `AGENTS.md` has overlapping notes — read them before touching config or plumbing.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                                  # then fill in keys
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 # dashboard at http://127.0.0.1:8000/
```

- `PORT` in `.env` only applies to `python main.py`; the `uvicorn` CLI ignores it. `.env` is read once at import (`backend/config.py`), so restart after editing it.
- Real phone calls need a public HTTPS origin (`ngrok http <uvicorn port>`) set as `PUBLIC_BASE_URL`; restart after the tunnel URL changes.
- Docker: `docker-compose.yml` runs `uvicorn main:app` on 8000 and requires `.env` to exist.
- No lint, formatter, typecheck or CI config exists; don't invoke tools the repo lacks.

## Tests

`tests/test_*.py` are **standalone asyncio scripts, not pytest tests**; pytest is not installed or configured. They need a **running server** and default to `--base-url http://127.0.0.1:8010` (not 8000), so start uvicorn on 8010 or pass `--base-url`.

```bash
.venv/bin/python tests/test_web_call.py        # /ws/browser
.venv/bin/python tests/test_inbound_call.py    # /ws/plivo, simulated Plivo media
.venv/bin/python tests/test_outbound_call.py   # /ws/plivo, simulated Plivo media
.venv/bin/python tests/test_outbound_call.py --smoke   # answer XML/wiring only; no paid calls
```

- Everything except `--smoke` is end-to-end against the real paid APIs (AssemblyAI, Sarvam, active TTS), takes minutes and spends credits. There is no offline mode and no single-test selector; each file is one scenario.
- `--live` (inbound) and `--to +E164` (outbound) place or wait for real PSTN calls.
- Each run overwrites a JSON in `tests/results/`. These are generated artifacts, not fixtures.

## Architecture

All three call types (Plivo inbound, Plivo outbound, browser) share one pipeline. Transports in `backend/routes/websocket_routes.py` (`/ws/plivo`, `/ws/browser`, plus `/ws/events` for dashboard pushes) feed `VoiceAgent` in `backend/agents/voice_agent.py`. That file (~1000 lines) owns turn orchestration, streaming, barge-in, playout tracking and per-turn metrics, so most call-path work starts there.

- **Turn flow:** AssemblyAI `end_of_turn` starts an LLM→TTS response. Sarvam tokens are forwarded to TTS at sentence boundaries. Barge-in is triggered by `SpeechStarted`, or by a final transcript arriving while the agent is `THINKING`/`SPEAKING`. It cancels the turn, abandons the TTS context and sends `clearAudio` (Plivo) or `clear` (browser). The transport is muted *before* the clear so a racing TTS chunk can't leak through. The agent stays in `SPEAKING` until estimated playout drains (or Plivo's `playedStream` checkpoint arrives), so the audible tail stays interruptible.
- **TTS providers are pluggable** via `backend/services/tts_factory.py`. A new provider must implement the existing contract `create_context → send_text → close_context → audio`, as Inworld (persistent WS; `INWORLD_FLUSH_PER_CHUNK` on = manual mode with a flush per chunk, off = `SENTENCE_BOUNDARY` auto mode that waits for a sentence end) and Speechify (streaming HTTP, per-sentence concurrent requests played back in order) and Sarvam Bulbul v4 Flash (persistent WS, `text`+`flush` per sentence, one `final` event per flush) do. Sarvam has no cancel message, so its `abandon_context` drops the socket and reconnects in the background. All three emit native 8 kHz μ-law, so there is no transcoding on the way out.
- **Audio contract:** 8 kHz mono μ-law on the Plivo leg and the TTS output; 16 kHz PCM16 into AssemblyAI. Conversion is in `backend/utils/audio_utils.py`. Resampling is **stateful across chunks**, so keep one resampler per stream and never create one per chunk. (`docs/overall_flow.md` says the browser sends 16 kHz PCM16, whereas the README says 8 kHz μ-law; check `script.js` and `/ws/browser` before relying on either.)
- **State is in-memory and single-process:** `backend/session_store.py` holds the call registry, usage and cost math, and the event bus. Runtime model config lives in `backend/config.py`. Finished calls are additionally written once, at call end, to SQLite (`backend/call_db.py`, `DB_PATH`, default `data/calls.db`) from a worker thread; the Cost & latency page (`GET /api/history`, and `/api/calls/{id}` falling back to the DB) reads from it. Never run multiple workers or replicas. `POST /api/config` (also used by the dashboard) changes model/TTS/prompt settings for **new calls only**; the `.env` values are just startup defaults.
- **Pricing:** `backend/pricing.py` holds static list prices plus a fixed `USD_INR`, and the `PRICING_*` env vars override them. LLM cost is per token (input, cached input and output, from the API's usage including `cached_tokens`; `llm_cost` in `session_store.py`). TTS cost is priced per record by provider and, for Inworld, by model (`settings.tts_rate(provider, model)`). Costs are estimates by design, and ASR is billed on WebSocket session duration, so the STT session must be closed when the call ends.
- **Warm-up:** `backend/services/warmup.py` opens real provider sessions in the background at startup. Failures are reported at `/api/warmup` and never fatal, and an inactive provider with an error (e.g. no Inworld credits) doesn't affect the active one.
- **Frontend** is plain `frontend/{index,script,style}` served by FastAPI from `/static` with `/` returning `index.html`. There is no framework or build step.

## Provider docs first

Before debugging latency, quality or cost for a TTS, LLM or STT provider, or before adding or changing one, **read that provider's official docs and blog first**. They often state the cause outright, and reading them takes far less time than benchmarking a black box. Examples from this project: Inworld's `SENTENCE_BOUNDARY` mode holds text until a sentence ends (so long first sentences delay first audio), and Sarvam's `flush` bypasses `min_buffer_size` (so each chunk is spoken at once).

- **What to look for:** streaming and latency best practices, message and field reference with defaults, limits (characters per message, concurrency, rate limits), pricing, and the changelog or blog for new models, modes and audio formats. Check for newer models or modes before settling on one.
- **Docs can disagree or change** (Sarvam's pages differ on WebSocket codecs; Inworld's diagram labels `SENTENCE_BOUNDARY` as Preview). When a fetched summary looks thin, open the page itself, and say which doc a claim comes from.
- **Measure after reading,** and keep it small. Provider accounts are free-tier or limited: Inworld ran out of credits on 2026-10-30 after benchmark runs. Do not run repeated paid benchmarks or live provider tests without need, and ask before running any large one.
- **Don't link `docs/` or `tests/` from pushed files:** both are in `.gitignore`.

| Provider | Official pages |
| --- | --- |
| Sarvam (LLM, TTS, pricing) | [Bulbul v4 Flash best practices](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/best-practices), [TTS WebSocket guide](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/streaming-api/web-socket), [WebSocket reference](https://docs.sarvam.ai/api-reference/text-to-speech/stream), [chat completions guide](https://docs.sarvam.ai/api/api-guides-tutorials/chat-completion/overview), [pricing](https://docs.sarvam.ai/api/getting-started/pricing) |
| Inworld (TTS) | [WebSocket guide](https://docs.inworld.ai/tts/synthesize-speech-websocket) (links to a latency best-practices guide), [API reference](https://docs.inworld.ai/api-reference/ttsAPI/texttospeech/synthesize-speech-websocket), [blog](https://inworld.ai/blog/tts-custom-pronunciation-timestamps-websockets), [pricing](https://inworld.ai/pricing) |
| Speechify (TTS) | [Streaming guide](https://docs.speechify.ai/build/guides/text-to-speech/streaming), [WebSocket input guide](https://docs.speechify.ai/build/guides/text-to-speech/streaming-text-input) (our client uses HTTP, not this), [latency guide](https://docs.speechify.ai/build/guides/text-to-speech/latency), [endpoint reference](https://docs.speechify.ai/build/api-reference/v1/audio/speech), [changelog](https://speechify.ai/blog/changelog), [pricing](https://speechify.ai/pricing) |
| AssemblyAI (STT) | [Streaming quickstart](https://www.assemblyai.com/docs/streaming/getting-started/transcribe-streaming-audio), [turn detection](https://www.assemblyai.com/docs/streaming/turn-detection), [WebSocket API](https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket), [pricing](https://www.assemblyai.com/pricing) |

## Gotchas

- `python-multipart` is required for Plivo's form-encoded webhooks. Without it `/plivo/inbound` returns 500 even though the server is up.
- Python 3.12+; 3.13 works only because `audioop-lts` replaces the removed stdlib `audioop`.
- `PLIVO_VALIDATE_SIGNATURE` is off by default for local ngrok use; the dashboard and API have no authentication.
