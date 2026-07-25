"""Tests for the browser voice transport and the data it feeds the dashboard."""

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Call, Lead
from app.services.tools import execute_tool

client = TestClient(app)


def test_leads_endpoint_returns_saved_leads():
    db = SessionLocal()
    try:
        call = Call(direction="browser", status="completed")
        db.add(call)
        db.commit()

        execute_tool(db, call.id, "save_lead", '{"name": "Aisha", "phone": "+15551112222", "reason": "pricing"}')
    finally:
        db.close()

    resp = client.get("/calls/leads")
    assert resp.status_code == 200
    leads = resp.json()
    assert any(lead["name"] == "Aisha" and lead["reason"] == "pricing" for lead in leads)


def test_leads_route_is_not_shadowed_by_call_id_route():
    """/calls/leads must resolve before /calls/{call_id}, not 404 as a call id."""
    assert client.get("/calls/leads").status_code == 200
    assert client.get("/calls/definitely-not-a-real-id").status_code == 404


def test_save_lead_tool_persists_row():
    db = SessionLocal()
    try:
        call = Call(direction="browser", status="in-progress")
        db.add(call)
        db.commit()

        result = execute_tool(db, call.id, "save_lead", '{"reason": "wants a callback"}')
        assert result["status"] == "ok"
        assert db.query(Lead).filter(Lead.call_id == call.id).count() == 1
    finally:
        db.close()


def test_unknown_tool_is_rejected_gracefully():
    db = SessionLocal()
    try:
        result = execute_tool(db, "some-call-id", "launch_rockets", "{}")
        assert result["status"] == "error"
    finally:
        db.close()


def test_malformed_tool_arguments_do_not_raise():
    db = SessionLocal()
    try:
        result = execute_tool(db, "some-call-id", "save_lead", "{not valid json")
        assert result["status"] == "error"
    finally:
        db.close()


def test_browser_session_rejects_when_openai_key_missing(monkeypatch):
    from app.routers import browser as browser_router

    monkeypatch.setattr(
        type(browser_router.settings), "openai_configured", property(lambda self: False)
    )
    with client.websocket_connect("/browser/session") as ws:
        msg = ws.receive_json()
    assert msg["event"] == "error"
    assert "OPENAI_API_KEY" in msg["message"]


def test_outbound_call_returns_503_without_twilio():
    resp = client.post("/calls/outbound", json={"to_number": "+15551234567"})
    assert resp.status_code == 503
    assert "Twilio" in resp.json()["detail"]
