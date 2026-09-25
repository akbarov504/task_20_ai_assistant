import os
from dataclasses import dataclass, field, replace
from dotenv import load_dotenv
from app.prompts import all_prompts
load_dotenv()

def _env_prefix() -> str:
    env = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "dev").lower()
    return "PROD" if "prod" in env else "DEV"

def _call_url(key: str) -> str:
    prefix = _env_prefix()
    return os.getenv(f"{prefix}_{key}") or os.getenv(key, "")

def _call_auth(key: str) -> str:
    prefix = _env_prefix()
    return os.getenv(f"{prefix}_{key}") or os.getenv(key, "")

@dataclass(frozen=True)
class Config:
    call_support_url: str = field(default_factory=lambda: _call_url("CALL_SUPPORT_URL"))
    call_service_url: str = field(default_factory=lambda: _call_url("CALL_SERVICE_URL"))
    call_wss_url: str = field(default_factory=lambda: _call_url("CALL_WSS_URL"))
    auth_username: str = field(default_factory=lambda: _call_auth("CALL_AUTH_USERNAME"))
    auth_password: str = field(default_factory=lambda: _call_auth("CALL_AUTH_PASSWORD"))
    auth_hash: str = os.getenv("CALL_AUTH_HASH", "")
    driver_cdl: str = os.getenv("DRIVER_CDL", "")
    call_type: str = os.getenv("CALL_TYPE", "SPECIAL")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
    gemini_voice: str = os.getenv("GEMINI_VOICE", "Kore")
    payload: str = os.getenv("PAYLOAD", None)
    payload_prompt_type: str = "DASH_CAM_EVENT_PROMPT_POLITE"
    user_pause_react_time: float = 20.0
    call_id: str = os.getenv("CALL_ID", "")
    recordings_dir: str = os.getenv("RECORDINGS_DIR", "app/utils/recordings")
    environment: str = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "dev"))
    gcs_bucket: str = os.getenv("GCS_BUCKET_NAME", "")
    gcs_creds_path: str = os.getenv("GCS_CREDS_PATH", "")
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: str = os.getenv("DB_PORT", "5432")
    db_name: str = os.getenv("DB_NAME", "ai_assistant_db")
    db_user: str = os.getenv("DB_USER", "akbarov")
    db_password: str = os.getenv("DB_PASSWORD", "akbarov")
    database_url: str = os.getenv("DATABASE_URL", "")
    analysis_webhook_url: str = os.getenv("ANALYSIS_WEBHOOK_URL", "")
    analyse_provider: str = os.getenv("ANALYSE_PROVIDER", "gemini")
    analyse_model: str = os.getenv("ANALYSE_MODEL", "gemini-3.7-flash")
    ai_tone: str = os.getenv("AI_TONE", "")

    # def __post_init__(self) -> None:
    #     if self.payload_prompt_type not in all_prompts:
    #         raise ValueError(
    #             f"payload_prompt_type must be one of: {tuple(all_prompts)}"
    #         )

    def with_event(self, driver_cdl: str, payload: str, call_type: str = None, payload_prompt_type: str = None, call_id: str = None, ai_tone: str = None) -> "Config":
        overrides = {
            "driver_cdl": driver_cdl,
            "payload": payload
        }
        print(f"with_event: driver_cdl={driver_cdl}, payload_prompt_type={payload_prompt_type}, call_id={call_id}, ai_tone={ai_tone}")

        if payload_prompt_type:
            overrides["payload_prompt_type"] = payload_prompt_type

        if call_type:
            overrides["call_type"] = call_type

        if call_id:
            overrides["call_id"] = call_id

        if ai_tone is not None:
            overrides["ai_tone"] = ai_tone

        return replace(self, **overrides)
