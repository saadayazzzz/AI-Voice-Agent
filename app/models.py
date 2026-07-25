import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_sid: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String, default="inbound")
    from_number: Mapped[str | None] = mapped_column(String, nullable=True)
    to_number: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="in-progress")
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    transcripts: Mapped[list["Transcript"]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="Transcript.created_at"
    )
    leads: Mapped[list["Lead"]] = relationship(back_populates="call", cascade="all, delete-orphan")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(String, ForeignKey("calls.id"))
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    call: Mapped["Call"] = relationship(back_populates="transcripts")


class Lead(Base):
    """Captured via the save_lead function tool during a call."""

    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(String, ForeignKey("calls.id"))
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    call: Mapped["Call"] = relationship(back_populates="leads")
