import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.logging_config import configure_logging
from app.routers import browser, calls, vapi, voice

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("voice_agent.main")

STATIC_DIR = Path(__file__).parent / "static"

init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Voice AI Agent starting up (env=%s)", settings.app_env)
    if not settings.openai_configured:
        logger.warning("OPENAI_API_KEY is not set — add it to .env, the agent cannot talk without it")
    if not settings.twilio_configured:
        logger.info("Twilio not configured — phone calls disabled, browser agent available")
    yield
    logger.info("Voice AI Agent shutting down")


app = FastAPI(
    title="Voice AI Agent",
    description="Voice agent powered by the OpenAI Realtime API, reachable from the browser or over the phone via Twilio",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(voice.router)
app.include_router(browser.router)
app.include_router(vapi.router)
app.include_router(calls.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/info")
def info():
    """Feature flags + agent settings the dashboard renders on load."""
    return {
        "service": "voice-ai-agent",
        "status": "running",
        "model": settings.openai_realtime_model,
        "voice": settings.openai_voice,
        "greeting": settings.agent_greeting,
        "openai_configured": settings.openai_configured,
        "twilio_configured": settings.twilio_configured,
        "vapi_configured": settings.vapi_outbound_configured,
        "phone_number": settings.twilio_phone_number or settings.vapi_phone_number,
    }


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")
