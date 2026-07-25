"""Bridges a client audio stream to the OpenAI Realtime API.

OpenAI Realtime handles speech-to-text, the LLM turn, and text-to-speech in a
single duplex WebSocket, so a bridge mostly relays audio frames both ways and
reacts to a handful of event types (transcripts, function calls, VAD-based
barge-in interruption).

Two transports share that logic via `BaseRealtimeBridge`:

  * `TwilioBridge`  — phone calls. G.711 u-law @ 8kHz, base64 inside Twilio's
    Media Streams envelope. Interruption uses Twilio's mark/clear protocol.
  * `BrowserBridge` — the web dashboard. PCM16 @ 24kHz (OpenAI's native
    format), plus JSON transcript/tool events pushed to the UI.

Neither transport transcodes audio: each one negotiates the session in the
format its client already speaks.

This targets the **GA** Realtime protocol. The older beta shape (the
`OpenAI-Beta: realtime=v1` header, flat `input_audio_format` strings, and
`response.audio.*` events) was retired by OpenAI in May 2026 and now closes the
socket with `invalid_request_error.beta_api_shape_disabled`.
"""

import asyncio
import json
import logging
import time

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Call, Transcript
from app.services.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger("voice_agent.realtime")
settings = get_settings()

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"


class BaseRealtimeBridge:
    """Owns the OpenAI side of the conversation; subclasses own the client side."""

    # Audio format negotiated with OpenAI for this transport, as a GA format
    # object (e.g. {"type": "audio/pcm", "rate": 24000}).
    audio_format: dict = {"type": "audio/pcm", "rate": 24000}

    def __init__(self, db: Session, call: Call):
        self.db = db
        self.call = call
        self.openai_ws: websockets.WebSocketClientProtocol | None = None
        self._assistant_transcript = ""

        # Barge-in bookkeeping: how far into the current reply we got before
        # the caller interrupted, so OpenAI's context matches what was heard.
        self.last_assistant_item: str | None = None
        self.response_started_at: float | None = None

    # ---- lifecycle -------------------------------------------------------

    async def run(self) -> None:
        url = f"{OPENAI_REALTIME_URL}?model={settings.openai_realtime_model}"
        headers = [("Authorization", f"Bearer {settings.openai_api_key}")]
        async with websockets.connect(url, additional_headers=headers) as openai_ws:
            self.openai_ws = openai_ws
            await self._init_session()
            await self._send_greeting()
            await asyncio.gather(
                self._client_to_openai(),
                self._openai_to_client(),
            )

    async def _init_session(self) -> None:
        await self.openai_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": settings.agent_system_prompt,
                # Audio output always carries a text transcript, so asking for
                # both modalities is rejected — "audio" alone gives us each.
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": self.audio_format,
                        "turn_detection": {"type": "server_vad"},
                        "transcription": {"model": "whisper-1"},
                    },
                    "output": {
                        "format": self.audio_format,
                        "voice": settings.openai_voice,
                    },
                },
                "tools": TOOL_DEFINITIONS,
            },
        }))

    async def _send_greeting(self) -> None:
        await self.openai_ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "instructions": f'Greet the caller now, briefly: "{settings.agent_greeting}"',
            },
        }))

    async def _append_audio(self, b64_audio: str) -> None:
        await self.openai_ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": b64_audio,
        }))

    # ---- OpenAI -> client ------------------------------------------------

    async def _openai_to_client(self) -> None:
        try:
            async for raw in self.openai_ws:
                response = json.loads(raw)
                event_type = response.get("type")

                if event_type == "error":
                    logger.error("OpenAI realtime error: %s", response)
                    continue

                if event_type == "response.output_audio.delta" and response.get("delta"):
                    if self.response_started_at is None:
                        self.response_started_at = time.monotonic()
                    if response.get("item_id"):
                        self.last_assistant_item = response["item_id"]
                    await self.send_audio(response["delta"])

                elif event_type == "input_audio_buffer.speech_started":
                    await self.handle_interruption()

                elif event_type == "response.output_audio.done":
                    self.response_started_at = None

                elif event_type == "response.output_audio_transcript.delta":
                    self._assistant_transcript += response.get("delta", "")

                elif event_type == "response.output_audio_transcript.done":
                    text = self._assistant_transcript.strip()
                    self._assistant_transcript = ""
                    if text:
                        self._save_transcript("assistant", text)
                        await self.on_transcript("assistant", text)

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    text = (response.get("transcript") or "").strip()
                    if text:
                        self._save_transcript("user", text)
                        await self.on_transcript("user", text)

                elif event_type == "response.output_item.done":
                    item = response.get("item", {})
                    if item.get("type") == "function_call":
                        await self._handle_function_call(item)
        except websockets.exceptions.ConnectionClosed:
            logger.info("OpenAI realtime websocket closed")

    async def _handle_function_call(self, item: dict) -> None:
        call_id = item["call_id"]
        name = item["name"]
        arguments = item.get("arguments", "{}")
        logger.info("Function call requested: %s(%s)", name, arguments)

        result = execute_tool(self.db, self.call.id, name, arguments)
        await self.on_tool_call(name, arguments, result)

        await self.openai_ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result),
            },
        }))
        await self.openai_ws.send(json.dumps({"type": "response.create"}))

    async def _truncate_assistant_audio(self, played_ms: int) -> None:
        """Tell OpenAI how much of its reply the user actually heard."""
        if not self.last_assistant_item:
            return
        await self.openai_ws.send(json.dumps({
            "type": "conversation.item.truncate",
            "item_id": self.last_assistant_item,
            "content_index": 0,
            "audio_end_ms": max(played_ms, 0),
        }))
        self.last_assistant_item = None
        self.response_started_at = None

    def _save_transcript(self, role: str, content: str) -> None:
        self.db.add(Transcript(call_id=self.call.id, role=role, content=content))
        self.db.commit()

    # ---- transport hooks (subclasses implement) --------------------------

    async def _client_to_openai(self) -> None:
        raise NotImplementedError

    async def send_audio(self, b64_audio: str) -> None:
        raise NotImplementedError

    async def handle_interruption(self) -> None:
        raise NotImplementedError

    async def on_transcript(self, role: str, text: str) -> None:
        """Optional: push a finished transcript line to the client UI."""

    async def on_tool_call(self, name: str, arguments: str, result: dict) -> None:
        """Optional: notify the client UI that a tool ran."""


class TwilioBridge(BaseRealtimeBridge):
    """Phone calls over Twilio Media Streams (G.711 u-law @ 8kHz)."""

    audio_format = {"type": "audio/pcmu"}

    def __init__(self, twilio_ws: WebSocket, db: Session, call: Call):
        super().__init__(db, call)
        self.twilio_ws = twilio_ws
        self.stream_sid: str | None = None

        # Twilio reports playback progress via marks; tracking them tells us
        # whether audio is still playing when the caller starts talking.
        self.latest_media_timestamp = 0
        self.response_start_timestamp: int | None = None
        self.mark_queue: list[str] = []

    async def _client_to_openai(self) -> None:
        try:
            async for raw in self.twilio_ws.iter_text():
                data = json.loads(raw)
                event = data.get("event")

                if event == "media":
                    self.latest_media_timestamp = int(data["media"]["timestamp"])
                    await self._append_audio(data["media"]["payload"])
                elif event == "start":
                    self.stream_sid = data["start"]["streamSid"]
                    logger.info("Twilio stream started: %s", self.stream_sid)
                elif event == "mark":
                    if self.mark_queue:
                        self.mark_queue.pop(0)
                elif event == "stop":
                    break
        except WebSocketDisconnect:
            logger.info("Twilio websocket disconnected")
        finally:
            if self.openai_ws is not None and self.openai_ws.state.name == "OPEN":
                await self.openai_ws.close()

    async def send_audio(self, b64_audio: str) -> None:
        await self.twilio_ws.send_json({
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": b64_audio},
        })
        if self.response_start_timestamp is None:
            self.response_start_timestamp = self.latest_media_timestamp
        await self.twilio_ws.send_json({
            "event": "mark",
            "streamSid": self.stream_sid,
            "mark": {"name": "responsePart"},
        })
        self.mark_queue.append("responsePart")

    async def handle_interruption(self) -> None:
        if not self.mark_queue or self.response_start_timestamp is None:
            return
        played_ms = self.latest_media_timestamp - self.response_start_timestamp
        await self._truncate_assistant_audio(played_ms)
        await self.twilio_ws.send_json({"event": "clear", "streamSid": self.stream_sid})
        self.mark_queue.clear()
        self.response_start_timestamp = None


class BrowserBridge(BaseRealtimeBridge):
    """Web dashboard mic session (PCM16 @ 24kHz, OpenAI's native format)."""

    audio_format = {"type": "audio/pcm", "rate": 24000}

    def __init__(self, browser_ws: WebSocket, db: Session, call: Call):
        super().__init__(db, call)
        self.browser_ws = browser_ws

    async def _client_to_openai(self) -> None:
        try:
            async for raw in self.browser_ws.iter_text():
                data = json.loads(raw)
                event = data.get("event")

                if event == "audio":
                    await self._append_audio(data["payload"])
                elif event == "stop":
                    break
        except WebSocketDisconnect:
            logger.info("Browser websocket disconnected")
        finally:
            if self.openai_ws is not None and self.openai_ws.state.name == "OPEN":
                await self.openai_ws.close()

    async def send_audio(self, b64_audio: str) -> None:
        await self._safe_send({"event": "audio", "payload": b64_audio})

    async def handle_interruption(self) -> None:
        if self.response_started_at is None:
            return
        # Browser plays chunks back-to-back in real time, so wall-clock elapsed
        # since the first chunk is a good proxy for how much was actually heard.
        played_ms = int((time.monotonic() - self.response_started_at) * 1000)
        await self._truncate_assistant_audio(played_ms)
        await self._safe_send({"event": "clear"})

    async def on_transcript(self, role: str, text: str) -> None:
        await self._safe_send({"event": "transcript", "role": role, "text": text})

    async def on_tool_call(self, name: str, arguments: str, result: dict) -> None:
        await self._safe_send({
            "event": "tool",
            "name": name,
            "arguments": arguments,
            "result": result,
        })

    async def _safe_send(self, payload: dict) -> None:
        """The UI can close its tab mid-response; a dead socket shouldn't
        crash the bridge and lose the transcript we already persisted."""
        try:
            await self.browser_ws.send_json(payload)
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Browser socket gone, dropping %s event", payload.get("event"))
