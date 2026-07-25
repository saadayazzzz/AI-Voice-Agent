from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Call

client = TestClient(app)


def test_incoming_call_returns_twiml_stream():
    resp = client.post(
        "/voice/incoming-call",
        data={"CallSid": "CA123", "From": "+15551234567", "To": "+15559876543"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "<Stream" in resp.text
    assert "wss://test.ngrok-free.app/voice/media-stream" in resp.text


def test_incoming_call_creates_call_record():
    client.post(
        "/voice/incoming-call",
        data={"CallSid": "CA456", "From": "+15551234567", "To": "+15559876543"},
    )
    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.call_sid == "CA456").first()
        assert call is not None
        assert call.from_number == "+15551234567"
        assert call.status == "in-progress"
    finally:
        db.close()


def test_status_callback_updates_call_status():
    client.post(
        "/voice/incoming-call",
        data={"CallSid": "CA789", "From": "+15551234567", "To": "+15559876543"},
    )
    resp = client.post(
        "/voice/status-callback",
        data={"CallSid": "CA789", "CallStatus": "completed"},
    )
    assert resp.status_code == 204

    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.call_sid == "CA789").first()
        assert call.status == "completed"
        assert call.ended_at is not None
    finally:
        db.close()
