"""Test environment.

Set before any app import so pydantic-settings picks these up instead of the
developer's real .env. Every provider credential is blanked deliberately: the
suite asserts the app degrades cleanly without them, and — more importantly —
a stray real value would let a test place an actual billable phone call.
Tests that need a configured provider monkeypatch it explicitly.
"""

import os

os.environ["OPENAI_API_KEY"] = "sk-test"
os.environ["PUBLIC_BASE_URL"] = "https://test.ngrok-free.app"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_PHONE_NUMBER"] = ""
os.environ["VAPI_API_KEY"] = ""
os.environ["VAPI_ASSISTANT_ID"] = ""
os.environ["VAPI_PHONE_NUMBER_ID"] = ""
os.environ["VAPI_SECRET"] = ""
