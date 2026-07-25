from functools import lru_cache

from twilio.rest import Client

from app.config import get_settings

settings = get_settings()


class TwilioNotConfigured(RuntimeError):
    """Raised when a phone-call feature is used without Twilio credentials."""


@lru_cache
def _client() -> Client:
    if not settings.twilio_configured:
        raise TwilioNotConfigured(
            "Twilio credentials are not set. Add TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN "
            "and TWILIO_PHONE_NUMBER to .env to enable phone calls."
        )
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def place_outbound_call(to_number: str) -> str:
    """Kick off an outbound call; Twilio will hit /incoming-call for TwiML once answered."""
    call = _client().calls.create(
        to=to_number,
        from_=settings.twilio_phone_number,
        url=f"{settings.public_base_url}/voice/incoming-call",
        status_callback=f"{settings.public_base_url}/voice/status-callback",
        status_callback_event=["completed"],
        status_callback_method="POST",
    )
    return call.sid
