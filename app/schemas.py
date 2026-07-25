import datetime

from pydantic import BaseModel, ConfigDict


class TranscriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: str
    created_at: datetime.datetime


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str | None
    phone: str | None
    reason: str | None
    created_at: datetime.datetime


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    call_sid: str | None
    direction: str
    from_number: str | None
    to_number: str | None
    status: str
    started_at: datetime.datetime
    ended_at: datetime.datetime | None
    transcripts: list[TranscriptOut] = []
    leads: list[LeadOut] = []


class OutboundCallRequest(BaseModel):
    to_number: str
