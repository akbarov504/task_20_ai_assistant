import os
import json
import logging
import aiohttp
from typing import Optional, Dict, Any, Tuple
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("webhook_service")

DEFAULT_DEV_WEBHOOK_URL = "https://dev-bot.greenlighteld.com/gateway/api/v1/public/safety/ai-calls/callback"
DEFAULT_PROD_WEBHOOK_URL = "https://bot.greenlighteld.com/gateway/api/v1/public/safety/ai-calls/callback"
DEFAULT_X_KEY = "5b10135a9b7855c538bee7713a11d15b45a6e05948ce7531dec979aeb0dabc39"

class AnalysisWebhookService:
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        x_key: Optional[str] = None,
        environment: Optional[str] = None,
    ):
        env_val = (environment or os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "").lower()
        if not env_val:
            call_url = os.getenv("CALL_SERVICE_URL", "").lower()
            if "prod" in call_url and "dev" not in call_url:
                env_val = "prod"
            else:
                env_val = "dev"

        self.environment = "prod" if "prod" in env_val else "dev"

        env_webhook = os.getenv("ANALYSIS_WEBHOOK_URL")
        if env_webhook:
            self.webhook_url = env_webhook
        elif webhook_url:
            self.webhook_url = webhook_url
        else:
            self.webhook_url = DEFAULT_PROD_WEBHOOK_URL if self.environment == "prod" else DEFAULT_DEV_WEBHOOK_URL

        self.x_key = x_key or os.getenv("WEBHOOK_X_KEY") or DEFAULT_X_KEY

    async def send_analysis_webhook(
        self,
        call_id: Any,
        audio_url: Optional[str],
        answer: Dict[str, Any],
        provider: str = "gemini",
        model: str = "gemini-3.7-flash",
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        if not self.webhook_url or not self.webhook_url.strip():
            logger.info(f"[WEBHOOK] Webhook URL not configured; skipping for call_id={call_id}")
            return False, None, "Webhook URL not configured"
        clean_call_id = int(str(call_id)) if str(call_id).isdigit() else call_id

        payload = {
            "call_id": clean_call_id,
            "audio_url": audio_url or "",
            "provider": provider or "gemini",
            "model": model or "gemini-3.7-flash",
            "answer": answer or {},
        }

        headers = {
            "Content-Type": "application/json",
            "X-Key": self.x_key,
        }

        try:
            logger.info(f"[WEBHOOK] Sending analysis for call_id={clean_call_id} to {self.webhook_url} (env={self.environment})...")
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers=headers,
                ) as resp:
                    resp_text = await resp.text()
                    is_ok = 200 <= resp.status < 300
                    if is_ok:
                        logger.info(f"[WEBHOOK] Successfully dispatched analysis for call_id={clean_call_id} (Status {resp.status})")
                    else:
                        logger.warning(f"[WEBHOOK] Endpoint returned status {resp.status} for call_id={clean_call_id}: {resp_text[:200]}")
                    return is_ok, resp.status, resp_text[:1000]
        except Exception as exc:
            logger.error(f"[WEBHOOK] Failed to send analysis webhook for call_id={clean_call_id}: {exc}")
            return False, None, str(exc)

_default_webhook_service: Optional[AnalysisWebhookService] = None

def get_webhook_service() -> AnalysisWebhookService:
    global _default_webhook_service
    if _default_webhook_service is None:
        _default_webhook_service = AnalysisWebhookService()
    return _default_webhook_service
