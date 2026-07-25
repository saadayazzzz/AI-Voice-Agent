import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Call, Lead
from app.schemas import CallOut, LeadOut, OutboundCallRequest
from app.services.twilio_client import TwilioNotConfigured, place_outbound_call
from app.services.vapi_client import VapiCallFailed, VapiNotConfigured
from app.services.vapi_client import place_outbound_call as place_vapi_call

logger = logging.getLogger("voice_agent.calls")
router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("", response_model=list[CallOut])
def list_calls(limit: int = 20, db: Session = Depends(get_db)):
    return db.query(Call).order_by(desc(Call.started_at)).limit(limit).all()


@router.get("/leads", response_model=list[LeadOut])
def list_leads(limit: int = 20, db: Session = Depends(get_db)):
    return db.query(Lead).order_by(desc(Lead.created_at)).limit(limit).all()


@router.get("/{call_id}", response_model=CallOut)
def get_call(call_id: str, db: Session = Depends(get_db)):
    call = db.query(Call).filter(Call.id == call_id).first()
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.post("/outbound", status_code=201)
def start_outbound_call(payload: OutboundCallRequest):
    """Trigger a test call over Twilio: the agent calls payload.to_number."""
    try:
        call_sid = place_outbound_call(payload.to_number)
    except TwilioNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"call_sid": call_sid, "status": "queued"}


@router.post("/vapi-outbound", status_code=201)
async def start_vapi_outbound_call(payload: OutboundCallRequest):
    """Trigger a test call over Vapi, which supplies the number and voice stack."""
    try:
        call = await place_vapi_call(payload.to_number)
    except VapiNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VapiCallFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"call_id": call.get("id"), "status": call.get("status", "queued")}
