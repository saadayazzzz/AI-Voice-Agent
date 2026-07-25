import json
import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.config import get_settings
from app.database import SessionLocal
from app.models import Call, utcnow
from app.services.openai_realtime import TwilioBridge

logger = logging.getLogger("voice_agent.voice")
router = APIRouter(prefix="/voice", tags=["voice"])
settings = get_settings()


@router.post("/incoming-call")
async def incoming_call(request: Request) -> Response:
    """Twilio hits this when a call comes in (or when we place an outbound
    call). We return TwiML that opens a bidirectional media stream back to
    us, and log the call row up front so it exists even if the WebSocket
    never connects."""
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From")
    to_number = form.get("To")
    direction = "inbound" if form.get("Direction", "inbound").startswith("inbound") else "outbound"

    db = SessionLocal()
    try:
        existing = db.query(Call).filter(Call.call_sid == call_sid).first()
        if existing is None:
            db.add(Call(
                call_sid=call_sid,
                direction=direction,
                from_number=from_number,
                to_number=to_number,
                status="in-progress",
            ))
            db.commit()
    finally:
        db.close()

    ws_url = f"{settings.public_ws_url}/voice/media-stream"
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{ws_url}" />'
        "</Connect></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/status-callback")
async def status_callback(request: Request) -> Response:
    form = await request.form()
    call_sid = form.get("CallSid")
    call_status = form.get("CallStatus")

    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.call_sid == call_sid).first()
        if call:
            call.status = call_status or call.status
            if call_status in ("completed", "failed", "busy", "no-answer", "canceled"):
                call.ended_at = utcnow()
            db.commit()
    finally:
        db.close()
    return Response(status_code=204)


@router.websocket("/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    db = SessionLocal()
    call: Call | None = None
    try:
        stream_sid: str | None = None
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event")
            if event == "connected":
                continue
            if event == "start":
                start = data["start"]
                stream_sid = start["streamSid"]
                call_sid = start.get("callSid")
                call = db.query(Call).filter(Call.call_sid == call_sid).first()
                if call is None:
                    call = Call(call_sid=call_sid, direction="inbound", status="in-progress")
                    db.add(call)
                    db.commit()
                break

        bridge = TwilioBridge(websocket, db, call)
        bridge.stream_sid = stream_sid
        await bridge.run()
    except WebSocketDisconnect:
        logger.info("Twilio media-stream websocket disconnected")
    except Exception:
        logger.exception("Unhandled error in media-stream bridge")
    finally:
        if call is not None:
            call.status = "completed"
            call.ended_at = utcnow()
            db.commit()
        db.close()
