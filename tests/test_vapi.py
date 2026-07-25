"""Tests for the Vapi phone-channel webhook.

Payloads mirror the shapes documented at docs.vapi.ai/server-url/events.
"""

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Call, Lead, Transcript

client = TestClient(app)

CALL = {
    "id": "vapi-call-001",
    "customer": {"number": "+15551230000"},
    "phoneNumber": {"number": "+15559990000"},
}


def _call_row(call_sid: str) -> Call | None:
    db = SessionLocal()
    try:
        return db.query(Call).filter(Call.call_sid == call_sid).first()
    finally:
        db.close()


def test_tool_call_saves_lead_and_returns_vapi_result_shape():
    resp = client.post("/vapi/webhook", json={
        "message": {
            "type": "tool-calls",
            "call": CALL,
            "toolCallList": [{
                "id": "toolcall-1",
                "name": "save_lead",
                "arguments": {"name": "Bilal", "phone": "+15551230000", "reason": "quote"},
            }],
        }
    })
    assert resp.status_code == 200

    body = resp.json()
    assert "results" in body
    result = body["results"][0]
    assert result["toolCallId"] == "toolcall-1"
    assert result["name"] == "save_lead"

    call = _call_row("vapi-call-001")
    assert call is not None and call.direction == "phone"

    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.call_id == call.id).first()
        assert lead is not None and lead.name == "Bilal"
    finally:
        db.close()


def test_end_of_call_report_saves_transcript_and_closes_call():
    """Uses the shape Vapi actually sends: the agent's turns are role "bot",
    and a "system" entry carrying the prompt leads the list."""
    payload = {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": "vapi-call-002", "customer": {"number": "+15557770000"}},
            "artifact": {
                "messages": [
                    {"role": "system", "message": "You are a friendly assistant..."},
                    {"role": "bot", "message": "Hi! Thanks for calling Acme Co."},
                    {"role": "user", "message": "I need a quote."},
                    {"role": "bot", "message": "Happy to help with that."},
                ]
            },
        }
    }
    assert client.post("/vapi/webhook", json=payload).status_code == 200

    call = _call_row("vapi-call-002")
    assert call.status == "completed"
    assert call.ended_at is not None

    db = SessionLocal()
    try:
        turns = db.query(Transcript).filter(Transcript.call_id == call.id).all()
        # The system prompt is dropped; both bot turns are kept as "assistant".
        assert [t.role for t in turns] == ["assistant", "user", "assistant"]
        assert turns[0].content == "Hi! Thanks for calling Acme Co."
    finally:
        db.close()


def test_docs_style_assistant_role_also_accepted():
    payload = {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": "vapi-call-007"},
            "artifact": {"messages": [{"role": "assistant", "message": "Hello."}]},
        }
    }
    assert client.post("/vapi/webhook", json=payload).status_code == 200

    call = _call_row("vapi-call-007")
    db = SessionLocal()
    try:
        turns = db.query(Transcript).filter(Transcript.call_id == call.id).all()
        assert len(turns) == 1 and turns[0].role == "assistant"
    finally:
        db.close()


def test_end_of_call_report_is_idempotent():
    """Vapi retries failed webhooks; a replay must not duplicate the transcript."""
    payload = {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": "vapi-call-003"},
            "artifact": {"messages": [{"role": "user", "message": "Hello there."}]},
        }
    }
    client.post("/vapi/webhook", json=payload)
    client.post("/vapi/webhook", json=payload)

    call = _call_row("vapi-call-003")
    db = SessionLocal()
    try:
        assert db.query(Transcript).filter(Transcript.call_id == call.id).count() == 1
    finally:
        db.close()


def test_status_update_marks_call_ended():
    client.post("/vapi/webhook", json={
        "message": {"type": "status-update", "call": {"id": "vapi-call-004"}, "status": "ringing"}
    })
    assert _call_row("vapi-call-004").status == "ringing"

    client.post("/vapi/webhook", json={
        "message": {"type": "status-update", "call": {"id": "vapi-call-004"}, "status": "ended"}
    })
    call = _call_row("vapi-call-004")
    assert call.status == "completed"
    assert call.ended_at is not None


def test_unknown_event_is_accepted_without_error():
    resp = client.post("/vapi/webhook", json={
        "message": {"type": "speech-update", "call": {"id": "vapi-call-005"}}
    })
    assert resp.status_code == 200


def test_vapi_outbound_returns_503_when_not_configured():
    resp = client.post("/calls/vapi-outbound", json={"to_number": "+923001234567"})
    assert resp.status_code == 503
    assert "VAPI_API_KEY" in resp.json()["detail"]


def test_vapi_outbound_posts_expected_payload(monkeypatch):
    from app.services import vapi_client

    captured = {}

    class FakeResponse:
        status_code = 201

        @staticmethod
        def json():
            return {"id": "vapi-out-1", "status": "queued"}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr(vapi_client.settings, "vapi_api_key", "key-123")
    monkeypatch.setattr(vapi_client.settings, "vapi_assistant_id", "asst-123")
    monkeypatch.setattr(vapi_client.settings, "vapi_phone_number_id", "num-123")
    monkeypatch.setattr(vapi_client.httpx, "AsyncClient", FakeClient)

    resp = client.post("/calls/vapi-outbound", json={"to_number": "+923001234567"})
    assert resp.status_code == 201
    assert resp.json()["call_id"] == "vapi-out-1"

    assert captured["url"] == "https://api.vapi.ai/call"
    assert captured["headers"]["Authorization"] == "Bearer key-123"
    assert captured["json"] == {
        "assistantId": "asst-123",
        "phoneNumberId": "num-123",
        "customer": {"number": "+923001234567"},
    }


def test_webhook_rejects_bad_secret_when_configured(monkeypatch):
    from app.routers import vapi as vapi_router

    monkeypatch.setattr(vapi_router.settings, "vapi_secret", "s3cret")

    unauthorised = client.post("/vapi/webhook", json={"message": {"type": "status-update"}})
    assert unauthorised.status_code == 401

    ok = client.post(
        "/vapi/webhook",
        json={"message": {"type": "status-update", "call": {"id": "vapi-call-006"}}},
        headers={"x-vapi-secret": "s3cret"},
    )
    assert ok.status_code == 200
