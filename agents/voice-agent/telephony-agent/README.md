# Streaming Telephony Voice Agent

A real-time phone agent built on **Plivo**, **AssemblyAI Universal Streaming**, **Sarvam 105B** and a switchable voice: **Sarvam Bulbul v4 Flash** (default), **Inworld TTS** or **Speechify Simba 3.5**. Inbound calls, outbound calls and browser test calls all run through one streaming pipeline. A plain-HTML dashboard shows live calls, transcripts, latency and estimated cost.

- **Audio:** 8 kHz mono μ-law on the Plivo/browser leg and TTS output; 16 kHz PCM16 into AssemblyAI.
- **Turns:** AssemblyAI `end_of_turn` starts the reply; Sarvam tokens go to TTS sentence by sentence.
- **Barge-in:** speech during a reply cancels LLM and TTS and clears the caller's playback. The agent stays interruptible until the queued audio has played out.
- **State:** live calls and runtime config are in memory. Finished calls are saved to SQLite (`data/calls.db`, `DB_PATH`).

## Quick start

Needs Python 3.12+ (3.13 works via `audioop-lts`), a Plivo number, and AssemblyAI and Sarvam keys. An Inworld or Speechify key is needed only if you use them.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in the keys
uvicorn main:app --host 0.0.0.0 --port 8000
```

Dashboard: <http://127.0.0.1:8000/>. `PORT` in `.env` only applies to `python main.py`. `.env` is read once, so restart after editing it.

Docker: `docker compose up -d --build` (needs `.env`; mounts `./data` so call history survives rebuilds).

### Environment

| Key | Purpose |
| --- | --- |
| `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER` | Plivo REST and your voice number (E.164) |
| `PUBLIC_BASE_URL` | Public HTTPS origin Plivo can reach, no trailing slash |
| `ASSEMBLYAI_API_KEY` | Speech to text |
| `SARVAM_API_KEY` | LLM, and Bulbul TTS when `TTS_PROVIDER=sarvam` |
| `TTS_PROVIDER` | `sarvam` (default), `inworld` or `speechify` |
| `INWORLD_API_KEY`, `SPEECHIFY_API_KEY` | Only for those providers |

Everything else (models, voice, prompt, timeouts, pricing, log level, warm-up) has a default and is listed in `.env.example`. Logs are one readable line per event (`LOG_FORMAT=json` for JSON); `LOG_TRANSCRIPTS=true` adds caller/agent text. The system prompt (Natalie, Hardline Landscape & Construction) is `DEFAULT_SYSTEM_PROMPT` in `backend/config.py`; `AGENT_SYSTEM_PROMPT` overrides it. The dashboard and API have no authentication, and secrets are never returned by the API.

## Phone setup

Plivo needs a public HTTPS origin: run `ngrok http <uvicorn port>` and set `PUBLIC_BASE_URL` to it. Restart after the tunnel URL changes, and update the Plivo application again (below).

Plivo application for the number:

- **Answer URL:** `https://<host>/plivo/inbound` (POST).
- **Hangup URL:** `https://<host>/plivo/status` (POST).

The answer webhook returns a bidirectional `<Stream>` to `wss://<host>/ws/plivo`. Outbound calls are placed from the dashboard (`POST /plivo/outbound {"to": "+E164"}`) and use `/plivo/answer?direction=outbound`. `python-multipart` is required for Plivo's form webhooks. `PLIVO_VALIDATE_SIGNATURE=true` enables signature checks (off by default for local tunnels).

A Plivo number points to one application, so two servers sharing a number overwrite each other's URLs. Calls then fail with "number is busy" (hangup cause 7011, `Error Reaching Answer URL`). To re-point the application (console: Voice → Applications, or the API):

```bash
curl -X POST "https://api.plivo.com/v1/Account/AUTH_ID/Application/APP_ID/" -u "AUTH_ID:AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"answer_url":"https://<host>/plivo/inbound","answer_method":"POST","hangup_url":"https://<host>/plivo/status","hangup_method":"POST"}'
```

A `202` with `"message": "changed"` means it worked; ring the number to confirm.

## Pricing (estimates)

Static list prices in `backend/pricing.py`, checked 30 Oct 2026, excluding GST, at a fixed `USD_INR = 95`.

| Part | Rate | Billed on |
| --- | --- | --- |
| AssemblyAI Universal-3.5/3.6 Pro Realtime | $0.45 / hour | session minutes |
| Sarvam 105B | ₹15 input / ₹5 cached input / ₹60 output per 1M tokens | tokens reported by the API |
| Sarvam Bulbul v4 Flash | ₹1,000 / 1M characters | characters sent |
| Inworld TTS-2 / TTS-2 Flash | $25 / $15 per 1M characters (On-Demand plan) | characters sent |
| Speechify Simba 3.5 | $10 / 1M characters (promo to 21 Nov 2026, then $20) | characters sent |

Input tokens include the cached part, so LLM cost is `(input − cached) × input rate + cached × cached rate + output × output rate`. Cached tokens come from the API's `prompt_tokens_details.cached_tokens`; cache writes are not priced (not listed). Each call is saved with the rates in force at its end. Calls saved before 30 Oct 2026 were priced on output characters only. Startup warm-up spend is not attributed to any call.

Override with `PRICING_ASR_PER_HOUR`, `PRICING_LLM_INPUT_PER_1M_TOKENS`, `PRICING_LLM_CACHED_PER_1M_TOKENS`, `PRICING_LLM_OUTPUT_PER_1M_TOKENS`, `PRICING_SARVAM_TTS_PER_1M_CHARS`, `PRICING_INWORLD_TTS_2_PER_1M_CHARS`, `PRICING_INWORLD_TTS_2_FLASH_PER_1M_CHARS` and `PRICING_TTS_PER_1M_CHARS` (Speechify). Restart after changing them.

## TTS providers

Chosen by `TTS_PROVIDER` or from the dashboard (`POST /api/config`, new calls only). All implement `create_context → send_text → close_context → audio` in `backend/services/tts_factory.py` and emit native 8 kHz μ-law.

| Provider | Model | Transport |
| --- | --- | --- |
| `sarvam` | `bulbul:v4-flash` | Persistent WebSocket; `text` + `flush` per sentence, one `final` event per flush |
| `inworld` | `inworld-tts-2` | Persistent bidirectional WebSocket |
| `speechify` | `simba-3.5` | Streaming HTTP, one request per sentence, played back in order |

- **Sarvam:** has no cancel message, so barge-in drops the socket and reconnects in the background. Voice names are model-specific (`<name>_<lang>_<style>`).
- **Inworld:** 20 voices as horizontal tabs in three groups: ten Indian English (`en`, tagged Indian by Inworld), seven Indian voices tagged Hindi that speak English clearly (Meher, Seema, Shreya, Riya, Aanya, Manoj, Aarav), and Ashley, Hana and Dennis.
- **Speechify:** pinned to `en-IN`. Max 2 concurrent requests across the whole process, so use it for 2–3 simultaneous calls at most.

## Dashboard

Plain HTML/CSS/JS served by FastAPI, no build step.

| Page | Purpose |
| --- | --- |
| Model config | Provider, model, voice, LLM and ASR settings, with an estimated voice cost per minute (900 chars/min). Applies to new calls. |
| Inbound / Outbound call | Number and webhook details; place an outbound call. |
| Live call | Conversation with per-turn TTFT/TTFA and a detection log. A summary opens when a call ends. |
| Web call | Browser microphone test through `/ws/browser`. |
| Cost & latency | Last 10 saved calls per type: median and p95 TTFT, TTFA and total turn time, cost per minute (INR first, USD below). **View** opens `#cost/<call_id>` with the cost split, **LLM usage** (duration, input, cached input and output tokens, characters) and turn-by-turn latency. |

**Network check:** the pill at the top of every page measures DNS, TCP and TLS connection time and jitter from this server to AssemblyAI, Sarvam, Inworld, Speechify and Plivo (also `POST /api/network-check`). It opens connections only, with no keys and no cost. **Run it before testing a call:** a slow or unstable network inflates TTFT and TTFA and delays turn endings, and it tells you whether to blame the connection or the code. It measures this server's path to the providers, not the caller's phone line or Plivo's media path.

The post-call summary shows the same LLM usage card with the cost arithmetic, so it can be checked against the Sarvam invoice.

## Architecture

```text
Plivo / browser ──μ-law 8 kHz──► /ws/plivo, /ws/browser ──► VoiceAgent
                                                             ├─► AssemblyAI STT (16 kHz PCM16)
                                                             ├─► Sarvam 105B (streaming SSE)
                                                             └─► Sarvam / Inworld / Speechify TTS ──► caller
```

`backend/agents/voice_agent.py` owns turn orchestration, barge-in, playout tracking and metrics. Resampling is stateful across chunks, so keep one resampler per stream. ASR is billed on WebSocket session time, so the STT session is closed when the call ends. At startup a background warm-up opens a real session on each provider; failures are reported at `/api/warmup` and are never fatal.

```text
main.py                  app, lifespan
backend/
  config.py              settings, runtime config
  pricing.py             list prices and USD/INR
  session_store.py       call registry, usage, cost, event bus
  call_db.py             SQLite call history
  agents/voice_agent.py  the call pipeline
  routes/                api, plivo, websocket
  services/              assemblyai_stt, sarvam_llm, *_tts, tts_factory, plivo_service, warmup, network_check
  utils/                 audio_utils, logging_config
frontend/                index.html, style.css, script.js
```

## Latest results

From the 12 saved calls (11 on Sarvam Bulbul v4 Flash, 1 web call on Speechify), 13.3 minutes and 51 measured turns, taken 30 Oct 2026 from the Cost & latency page. Interrupted turns and the greeting are excluded.

| Calls | Turns | TTFT median / p95 | TTFA median / p95 | Total turn median / p95 |
| --- | --- | --- | --- | --- |
| All (12) | 51 | 325 / 725 ms | 290 / 934 ms | **628 / 1,797 ms** |
| Inbound (7) | 35 | 320 / 850 ms | 278 / 535 ms | 606 / 1,208 ms |
| Web (5) | 16 | 386 / 633 ms | 340 / 1,604 ms | 757 / 1,940 ms |

TTFT is the LLM's time to first token. TTFA is the TTS's time to first audio after that token. Total turn is TTFT + TTFA per turn.

**Cost per minute:** about **₹1.4 (≈ $0.015)**: speech to text ₹0.66, text to speech ₹0.69 and language model ≈ ₹0.08. The LLM figure re-prices the saved token counts at the current Sarvam rates assuming almost no caching. Most of these calls were saved before token pricing existed, so their stored LLM cost is too low. The latest call (64 s) cost ₹1.57 with 59% of its input cached.

Caveats: samples are small, no outbound call has been made yet, and the web p95 TTFA is pulled up by the one Speechify call. Web calls have no phone network in the path. Latency is measured at the server, so it excludes the caller's phone-to-Plivo leg. The "ASR endpoint" delay still reads 0 and is not a reliable measurement. One inbound call received only about half of the wall-clock audio from Plivo, which delays AssemblyAI's end-of-turn; the cause is upstream and not fixed here.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Provider configuration, warm-up, active calls |
| GET / POST | `/api/warmup` | Warm-up status / re-run |
| GET / POST | `/api/config` | Read / update runtime model config |
| GET / POST | `/api/network-check` | Last / run a DNS, TCP and TLS check to each provider |
| GET | `/api/stats` | Session totals and costs |
| GET | `/api/history?type=all\|inbound\|outbound\|web` | Saved calls, medians, cost per minute |
| GET | `/api/calls/{id}` | Call record with turns (falls back to saved history) |
| POST | `/api/calls/{id}/hangup` | End an active call |
| GET | `/api/pricing` | Configured rates |
| POST | `/plivo/inbound`, `/plivo/outbound` | Inbound answer XML / place an outbound call |
| GET / POST | `/plivo/answer`, `/plivo/status` | Answer XML (uses Plivo's `Direction` unless `?direction=` is set) / Plivo callbacks |
| WebSocket | `/ws/plivo`, `/ws/browser`, `/ws/events` | Plivo media, browser audio, dashboard events |

## Limits

- **Single process.** The call registry, config and event bus are in memory, so never run extra workers or replicas. Rough ceiling: 20–50 simultaneous calls on one worker, set mostly by provider limits, and 2–3 with Speechify. There is no concurrent-call cap in the code.
- **Restarts** drop live calls and runtime config changes. Saved history survives.
- **English ASR.** The LLM and TTS can speak other languages, but recognition is English-first.
- **Costs are estimates.** Invoices are authoritative.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| "Number is busy" on a call | Plivo can't reach the answer URL (cause 7011). Re-point the application (see Phone setup) |
| Call connects but is silent | Application URL points at another server, or `PUBLIC_BASE_URL` is not the current tunnel |
| `/plivo/inbound` returns 500 | `python-multipart` missing; restart after installing |
| Browser call: "getUserMedia of undefined" | Browsers need HTTPS or `localhost` for the microphone |
| Warm-up shows `error` | `GET /api/warmup` has the message; check that provider's key and credits |
| Slow replies or delayed turn endings | Check the Network check pill first (high connect time or jitter), then warm-up |
| Slow first turn | A cold provider connection; check warm-up |
