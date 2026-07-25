import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Call, Transcript, utcnow
from app.services.tools import execute_tool

logger = logging.getLogger("voice_agent.vapi")
router = APIRouter(prefix="/vapi", tags=["vapi"])
settings = get_settings()


def _get_or_create_call(db: Session, vapi_call: dict) -> Call:
    call_id = vapi_call.get("id")
    call = db.query(Call).filter(Call.call_sid == call_id).first()
    if call is not None:
        return call

    customer = vapi_call.get("customer") or {}
    call = Call(
        call_sid=call_id,
        direction="phone",
        from_number=customer.get("number"),
        to_number=vapi_call.get("phoneNumber", {}).get("number"),
        status="in-progress",
    )
    db.add(call)
    db.commit()
    return call


# Vapi labels the agent's turns "bot" in artifact.messages (the docs say
# "assistant"); both are accepted so either shape maps onto our schema. The
# leading "system" entry is the prompt itself and is deliberately dropped.
_ROLE_MAP = {"bot": "assistant", "assistant": "assistant", "user": "user"}


def _save_final_transcript(db: Session, call: Call, artifact: dict) -> int:
    """Persist the finished conversation.

    Vapi streams partial transcripts during the call and sends the complete,
    ordered turn list once at the end, so the end-of-call report is the only
    place worth writing from — partials would produce duplicate half-sentences.
    """
    if db.query(Transcript).filter(Transcript.call_id == call.id).count():
        return 0

    saved = 0
    for turn in artifact.get("messages", []):
        role = _ROLE_MAP.get(turn.get("role"))
        content = (turn.get("message") or "").strip()
        if not role or not content:
            continue
        db.add(Transcript(call_id=call.id, role=role, content=content))
        saved += 1
    db.commit()
    return saved


@router.post("/webhook")
async def vapi_webhook(request: Request, x_vapi_secret: str | None = Header(default=None)):
    if settings.vapi_secret:
        if not x_vapi_secret or not hmac.compare_digest(x_vapi_secret, settings.vapi_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    body = await request.json()
    message = body.get("message", {})
    event = message.get("type")
    vapi_call = message.get("call") or {}

    db = SessionLocal()
    try:
        if event == "tool-calls":
            return _handle_tool_calls(db, message, vapi_call)

        if event == "end-of-call-report":
            call = _get_or_create_call(db, vapi_call)
            saved = _save_final_transcript(db, call, message.get("artifact") or {})
            call.status = "completed"
            call.ended_at = utcnow()
            db.commit()
            logger.info("Vapi call %s ended (%s turns saved)", call.call_sid, saved)

        elif event == "status-update":
            call = _get_or_create_call(db, vapi_call)
            status = message.get("status")
            if status:
                call.status = "completed" if status == "ended" else status
                if status == "ended":
                    call.ended_at = utcnow()
                db.commit()

        return {"received": True}
    finally:
        db.close()


def _handle_tool_calls(db: Session, message: dict, vapi_call: dict) -> dict:
    call = _get_or_create_call(db, vapi_call)
    results = []

    for tool_call in message.get("toolCallList", []):
        name = tool_call.get("name")
        arguments = tool_call.get("arguments", tool_call.get("parameters", {}))
        logger.info("Vapi tool call: %s(%s)", name, arguments)

        result = execute_tool(db, call.id, name, json.dumps(arguments or {}))
        results.append({
            "name": name,
            "toolCallId": tool_call.get("id"),
            "result": json.dumps(result),
        })

    return {"results": results}
