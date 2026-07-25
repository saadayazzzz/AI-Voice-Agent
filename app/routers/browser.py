"""Browser voice sessions — talk to the agent from the dashboard, no phone needed."""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.database import SessionLocal
from app.models import Call, utcnow
from app.services.openai_realtime import BrowserBridge

logger = logging.getLogger("voice_agent.browser")
router = APIRouter(prefix="/browser", tags=["browser"])
settings = get_settings()


@router.websocket("/session")
async def browser_session(websocket: WebSocket) -> None:
    await websocket.accept()

    if not settings.openai_configured:
        await websocket.send_json({
            "event": "error",
            "message": "OPENAI_API_KEY is not set. Add it to your .env file and restart the server.",
        })
        await websocket.close()
        return

    db = SessionLocal()
    call = Call(direction="browser", from_number="web-dashboard", status="in-progress")
    db.add(call)
    db.commit()

    try:
        await websocket.send_json({"event": "ready", "call_id": call.id})
        bridge = BrowserBridge(websocket, db, call)
        await bridge.run()
    except WebSocketDisconnect:
        logger.info("Browser session disconnected (call %s)", call.id)
    except Exception as exc:
        logger.exception("Browser session failed (call %s)", call.id)
        # Surface the failure in the UI instead of a silent dead microphone.
        try:
            await websocket.send_json({"event": "error", "message": str(exc)})
        except (WebSocketDisconnect, RuntimeError):
            pass
    finally:
        call.status = "completed"
        call.ended_at = utcnow()
        db.commit()
        db.close()
