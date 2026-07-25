import logging

import httpx

from app.config import get_settings

logger = logging.getLogger("voice_agent.vapi_client")
settings = get_settings()

VAPI_API_URL = "https://api.vapi.ai/call"


class VapiNotConfigured(RuntimeError):
    """Raised when an outbound Vapi call is attempted without credentials."""


class VapiCallFailed(RuntimeError):
    """Raised when Vapi rejects the call request."""


async def place_outbound_call(to_number: str) -> dict:
    if not settings.vapi_outbound_configured:
        raise VapiNotConfigured(
            "Vapi outbound calling is not set up. Add VAPI_API_KEY, VAPI_ASSISTANT_ID "
            "and VAPI_PHONE_NUMBER_ID to .env."
        )

    payload = {
        "assistantId": settings.vapi_assistant_id,
        "phoneNumberId": settings.vapi_phone_number_id,
        "customer": {"number": to_number},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            VAPI_API_URL,
            headers={"Authorization": f"Bearer {settings.vapi_api_key}"},
            json=payload,
        )

    if response.status_code >= 400:
        # Vapi puts the useful reason in the body, not the status line.
        logger.error("Vapi call failed (%s): %s", response.status_code, response.text)
        raise VapiCallFailed(f"Vapi returned {response.status_code}: {response.text}")

    body = response.json()
    logger.info("Vapi outbound call queued: %s", body.get("id"))
    return body
