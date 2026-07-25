from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_info_reports_feature_flags():
    resp = client.get("/api/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "voice-ai-agent"
    # conftest supplies a dummy OpenAI key but no Twilio credentials.
    assert body["openai_configured"] is True
    assert body["twilio_configured"] is False


def test_dashboard_is_served_at_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Voice AI Agent" in resp.text


def test_static_assets_are_served():
    for asset in ("/static/app.js", "/static/styles.css", "/static/mic-worklet.js"):
        assert client.get(asset).status_code == 200, asset
