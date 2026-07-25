"""Function-calling tools the voice agent can invoke mid-conversation.

Add new tools by: (1) describing them in TOOL_DEFINITIONS (OpenAI Realtime
function-call schema) and (2) implementing them in TOOL_HANDLERS with a
matching name. The bridge in services/openai_realtime.py dispatches to these
generically, so no other code needs to change to add a new tool.
"""

import json
import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import Lead

logger = logging.getLogger("voice_agent.tools")

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "save_lead",
        "description": (
            "Save the caller's contact details and reason for calling. "
            "Use this whenever the caller shares their name, phone number, "
            "or the reason they're calling, so a human can follow up later."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Caller's full name"},
                "phone": {"type": "string", "description": "Caller's callback phone number"},
                "reason": {"type": "string", "description": "Why the caller is reaching out"},
            },
            "required": ["reason"],
        },
    },
]


def _save_lead(db: Session, call_id: str, args: dict[str, Any]) -> dict[str, Any]:
    lead = Lead(
        call_id=call_id,
        name=args.get("name"),
        phone=args.get("phone"),
        reason=args.get("reason"),
    )
    db.add(lead)
    db.commit()
    logger.info("Saved lead for call %s: %s", call_id, args)
    return {"status": "ok", "message": "Lead saved."}


TOOL_HANDLERS: dict[str, Callable[[Session, str, dict[str, Any]], dict[str, Any]]] = {
    "save_lead": _save_lead,
}


def execute_tool(db: Session, call_id: str, tool_name: str, arguments_json: str) -> dict[str, Any]:
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return {"status": "error", "message": "Invalid arguments JSON"}
    try:
        return handler(db, call_id, args)
    except Exception:
        logger.exception("Tool %s failed", tool_name)
        return {"status": "error", "message": "Tool execution failed"}
