from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_realtime_model: str = "gpt-realtime"
    openai_voice: str = "marin"

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None

    vapi_secret: str | None = None
    vapi_phone_number: str | None = None
    vapi_api_key: str | None = None
    vapi_assistant_id: str | None = None
    vapi_phone_number_id: str | None = None

    public_base_url: str = "http://localhost:8000"

    agent_system_prompt: str = "You are a helpful, concise voice assistant."
    agent_greeting: str = "Hello! How can I help you today?"

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
    def vapi_outbound_configured(self) -> bool:
        return all([self.vapi_api_key, self.vapi_assistant_id, self.vapi_phone_number_id])

    @property
    def public_ws_url(self) -> str:
        """Convert the public https base URL into the wss:// URL Twilio streams to."""
        return self.public_base_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
