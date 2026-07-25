"""Test environment.

Set before any app import so pydantic-settings picks these up instead of the
developer's real .env. Twilio is deliberately left unset — the suite asserts
the app degrades cleanly to browser-only mode without phone credentials.
"""

import os

os.environ["OPENAI_API_KEY"] = "sk-test"
os.environ["PUBLIC_BASE_URL"] = "https://test.ngrok-free.app"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_PHONE_NUMBER"] = ""
