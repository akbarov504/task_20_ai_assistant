import aiohttp
import time
import base64
import json
import asyncio

class CallAuth:
    def __init__(self, cfg):
        self.cfg = cfg
        self._token = None
        self._exp = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _jwt_exp(token):
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            return int(data.get("exp", 0))
        except Exception:
            return 0

    async def get_token(self):
        if self._token and self._exp - time.time() >= 60:
            return self._token

        async with self._lock:
            if self._token and self._exp - time.time() >= 60:
                return self._token

            url = f"{self.cfg.call_support_url}/api/support/auth"
            payload = {
                "username": self.cfg.auth_username,
                "password": self.cfg.auth_password,
                "hash": self.cfg.auth_hash,
            }

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    body = await response.json(content_type=None)

            token = body.get("token")
            content = body.get("content")
            if not token and isinstance(content, dict):
                token = content.get("token")

            if not token:
                desc = body.get("result", {}).get("description", "No token returned")
                raise RuntimeError(f"Call authentication failed: {desc}")

            self._token = token
            self._exp = self._jwt_exp(token)
            return token
