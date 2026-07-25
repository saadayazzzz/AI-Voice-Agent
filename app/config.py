from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI — the only credential required to run the browser-based agent.
    # Empty is allowed so the server still boots and the dashboard can say
    # exactly what's missing, rather than dying on a pydantic traceback.
    openai_api_key: str = ""
    openai_realtime_model: str = "gpt-realtime"
    openai_voice: str = "marin"

    # Twilio — optional. Only needed for real phone calls; leaving these unset
    # still lets the whole agent be exercised from the browser dashboard.
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None

    # Public URL Twilio dials back into (ngrok/deployed domain). Not used by
    # the browser transport, so it defaults to localhost.
    public_base_url: str = "http://localhost:8000"

    # Agent behavior
    agent_system_prompt: str = "You are a helpful, concise voice assistant."
    agent_greeting: str = "Hello! How can I help you today?"

    # App
    app_env: str = "development"
    port: int = 8000
    database_url: str = "sqlite:///./voice_agent.db"
    log_level: str = "INFO"

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def twilio_configured(self) -> bool:
        return all([self.twilio_account_sid, self.twilio_auth_token, self.twilio_phone_number])

    @property
    def public_ws_url(self) -> str:
        """Convert the public https base URL into the wss:// URL Twilio streams to."""
        return self.public_base_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
