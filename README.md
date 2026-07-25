---
title: Voice AI Agent
emoji: 🎙️
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 8000
pinned: false
---

# Voice AI Agent

A real-time voice AI agent you can talk to **from the browser** or **over the phone**.
Audio streams to a FastAPI backend which bridges it to the **OpenAI Realtime API** —
speech-to-text, the LLM turn, and text-to-speech happen inside one duplex WebSocket.
Calls, transcripts, and captured leads are persisted and shown live on a dashboard.

**To try it:** open the deployed URL, click the mic, and start talking — no phone
number or signup needed. The browser path is the verified one; the Twilio phone
path is [implemented but untested](#adding-phone-calls-twilio--optional-untested)
since it requires a paid number.

## Architecture

```
                 ┌─ Browser dashboard (mic) ──── PCM16 @ 24kHz ──┐
                 │                                               │
                 │                                               ▼
Caller ⇄ Twilio ─┴─ Media Streams ── G.711 u-law @ 8kHz ──▶  FastAPI  ⇄  OpenAI Realtime API
                                                                │
                                                                ▼
                                                    SQLite (calls, transcripts, leads)
```

Both transports share one `BaseRealtimeBridge`; each subclass only declares the
audio format its client speaks and how to push audio back. **No transcoding
happens anywhere** — the OpenAI session is negotiated in the client's native
format, which keeps latency down and the code small.

- **Voice AI**: OpenAI Realtime API **GA** (`gpt-realtime`) — server-side VAD, barge-in interruption
- **Telephony**: Twilio Voice + Media Streams *(optional)*
- **Backend**: Python 3.12 + FastAPI
- **Frontend**: dependency-free HTML/CSS/JS (Web Audio API + AudioWorklet), no build step
- **Database**: SQLite via SQLAlchemy — point `DATABASE_URL` at Postgres for production, no code changes
- **Function calling**: the agent calls `save_lead` mid-conversation to capture caller details

## Quick start (browser only — no phone number needed)

You need **one** credential: an OpenAI API key with Realtime access.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements-dev.txt
```

Open `.env` and paste your key:

```
OPENAI_API_KEY=sk-...
```

Start the server:

```bash
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000**, click the mic, allow microphone access, and talk.
The agent greets you, responds in real time, and you can interrupt it mid-sentence.

> Requires Python 3.12 or 3.13. On Python 3.14 `pydantic-core` has no prebuilt
> wheel yet and pip will try (and likely fail) to compile it from Rust source.

### What the dashboard gives you

| Panel | What it does |
| --- | --- |
| **Live session** | Mic button, call timer, and dual audio meters for you and the agent |
| **Transcript** | Both sides of the conversation, streamed in as each turn completes |
| **Recent calls** | Every session — browser and phone — with turn counts and status |
| **Captured leads** | Rows the agent saved by calling the `save_lead` tool |
| **Phone call** | Dial out via Twilio (hidden until Twilio credentials are set) |

## Adding phone calls (Twilio) — optional, untested

> **Status:** the Twilio path is code-complete but has **not been run against a
> live phone call**, because it needs a paid phone number. The browser agent
> above is the verified, working path. Treat this section as instructions rather
> than a demonstrated feature.


1. Sign up at twilio.com, then from the console dashboard copy your **Account SID** and **Auth Token**. Buy or use a trial phone number.
2. Fill these into `.env`:
   ```
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_PHONE_NUMBER=+1...
   ```
3. Expose your local server so Twilio can reach it:
   ```bash
   ngrok http 8000
   ```
   Copy the `https://xxxx.ngrok-free.app` URL into `.env` as `PUBLIC_BASE_URL`, then restart uvicorn.
4. In the Twilio Console go to **Phone Numbers → Manage → Active Numbers → (your number) → Voice Configuration**. Under "A call comes in" set:
   - Webhook: `https://xxxx.ngrok-free.app/voice/incoming-call`
   - Method: `HTTP POST`
5. Save, then call your Twilio number.

Trial accounts can only reach **verified** numbers — verify your phone under
Twilio Console → Phone Numbers → Verified Caller IDs, or upgrade the account.

To have the agent call *you*, use the dashboard's Phone card or:

```bash
curl -X POST http://localhost:8000/calls/outbound \
  -H "Content-Type: application/json" -d '{"to_number": "+1XXXXXXXXXX"}'
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Dashboard UI |
| `GET` | `/health` | Liveness probe |
| `GET` | `/api/info` | Model, voice, and which integrations are configured |
| `WS` | `/browser/session` | Browser mic session (PCM16 @ 24kHz) |
| `POST` | `/voice/incoming-call` | Twilio webhook → returns TwiML opening a media stream |
| `WS` | `/voice/media-stream` | Twilio Media Streams audio (G.711 u-law @ 8kHz) |
| `POST` | `/voice/status-callback` | Twilio call-completion webhook |
| `GET` | `/calls` | Recent calls with transcripts and leads |
| `GET` | `/calls/leads` | Recently captured leads |
| `GET` | `/calls/{id}` | A single call |
| `POST` | `/calls/outbound` | Place an outbound call (503 if Twilio isn't configured) |

Interactive docs at `/docs`.

## Tests

```bash
pytest
```

Covers TwiML generation, call/status persistence, the leads API and its route
ordering, tool dispatch (including malformed arguments and unknown tools), the
graceful-degradation paths when credentials are absent, and static asset serving.

## Project layout

```
app/
  main.py                    FastAPI app, static mount, /api/info
  config.py                  Settings from .env; integrations degrade gracefully when unset
  database.py, models.py     SQLAlchemy engine/session, Call/Transcript/Lead tables
  schemas.py                 Pydantic response models
  routers/
    voice.py                 Twilio webhooks + media-stream WebSocket
    browser.py               Browser mic-session WebSocket
    calls.py                 Calls/leads REST API
  services/
    openai_realtime.py       BaseRealtimeBridge + TwilioBridge + BrowserBridge
    twilio_client.py         Outbound calls (lazy client, optional credentials)
    tools.py                 Function-calling definitions + handlers
  static/
    index.html, styles.css   Dashboard
    app.js                   WebSocket client, PCM16 encode/decode, playback scheduling
    mic-worklet.js           AudioWorklet mic capture
tests/
Dockerfile
```

## Deploying (free, no credit card)

The app needs a host that supports **long-lived WebSocket connections**, which
rules out Vercel's serverless functions. Any Docker host works — the `Dockerfile`
binds `$PORT` and runs as a non-root user.

### Hugging Face Spaces (recommended — free, stays up)

The YAML frontmatter at the top of this README is the Space config, so no extra
files are needed.

1. Create a Space at [huggingface.co/new-space](https://huggingface.co/new-space):
   pick **Docker → Blank**, visibility **Public**.
2. Push this repo to the Space's git remote:
   ```bash
   git remote add space https://huggingface.co/spaces/<user>/<space-name>
   git push space main
   ```
3. In the Space's **Settings → Variables and secrets**, add a secret
   `OPENAI_API_KEY` with your key. Never commit it.
4. Wait for the build, then open the Space URL and click the mic.

### Render

`render.yaml` is a Blueprint for Render's free plan: **New + → Blueprint**, select
the repo, and paste `OPENAI_API_KEY` when prompted. Note the free plan **sleeps
after ~15 minutes** of inactivity and takes about a minute to wake.

### Notes that apply to any host

- **HTTPS is required** for microphone access. Browsers only grant mic
  permission on secure origins (or `localhost`), so a plain `http://` host
  will not work.
- **SQLite is ephemeral here.** The container writes to `/tmp`, so calls and
  transcripts reset on redeploy. Point `DATABASE_URL` at a managed Postgres to
  persist them — no code changes required.
- **The Realtime API bills per minute of audio** regardless of where you host.
  `gpt-realtime-mini` is a cheaper alternative to the `gpt-realtime` default.

## Customizing the agent

- **Personality**: `AGENT_SYSTEM_PROMPT` in `.env`
- **Greeting**: `AGENT_GREETING`
- **Voice**: `OPENAI_VOICE` — `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `marin`, `cedar` (OpenAI recommends `marin`/`cedar`)
- **Model**: `OPENAI_REALTIME_MODEL` — `gpt-realtime` by default; `gpt-realtime-mini` is cheaper
- **New capabilities**: add a tool in `app/services/tools.py` — describe it in `TOOL_DEFINITIONS`, implement it in `TOOL_HANDLERS`. The bridge dispatches generically, so nothing else changes.

## Design notes

- **Built against the GA Realtime protocol.** OpenAI retired the beta API shape
  in May 2026 — the `OpenAI-Beta: realtime=v1` header, flat `input_audio_format`
  strings, and `response.audio.*` events now close the socket with
  `beta_api_shape_disabled`. This code uses the GA shape: `session.type:
  "realtime"`, audio format objects (`{"type": "audio/pcm", "rate": 24000}` /
  `{"type": "audio/pcmu"}`), config nested under `audio.input` / `audio.output`,
  and `response.output_audio.*` events.
- **One bridge, two transports.** Phone and browser sessions differ only in audio format and how audio is pushed back; shared OpenAI logic lives in the base class, so a fix to turn-handling applies to both.
- **Barge-in is handled properly.** When the caller starts talking over the agent, the in-flight response is truncated at the point actually heard *and* the client's playback buffer is flushed — so the model's context matches what the human experienced.
- **Graceful degradation over hard failure.** Missing Twilio credentials disable phone features and surface a clear reason in the UI; a missing OpenAI key still serves the dashboard and tells you exactly what to fix, instead of a boot-time traceback.
- **Chunked mic capture.** The AudioWorklet batches to ~85ms frames instead of forwarding every 128-sample block, cutting WebSocket messages 16x with no perceptible latency cost.
- **Scoped DB sessions.** Sessions are per-request/per-call rather than a shared global, so a long-running call can't hold a connection hostage.
- Structured logging, a `/health` probe, and a Dockerfile for portable deployment.
