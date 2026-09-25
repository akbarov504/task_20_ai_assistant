import aiohttp

STUN_FALLBACK = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
    {"urls": "stun:stun2.l.google.com:19302"},
]

async def get_ice_config(cfg, token):
    url = f"{cfg.call_service_url}/call-service/support/calls/webrtc/ice-config"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                body = await response.json(content_type=None)

        content = body.get("content") or {}
        servers = []

        for item in content.get("iceServers", []):
            urls = item.get("urls", [])
            if isinstance(urls, str):
                urls = [urls]
            for url_value in urls:
                server = {"urls": url_value}
                if item.get("username") is not None and item.get("credential") is not None:
                    server["username"] = item["username"]
                    server["credential"] = item["credential"]
                servers.append(server)

        policy = content.get("iceTransportPolicy")
        if policy not in ("all", "relay"):
            policy = "all"

        return {"iceServers": servers or STUN_FALLBACK, "iceTransportPolicy": policy}
    except Exception as exc:
        print(f"[ICE] config unavailable, using STUN fallback: {exc}")
        return {"iceServers": STUN_FALLBACK, "iceTransportPolicy": "all"}
