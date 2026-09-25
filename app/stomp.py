import asyncio
import json
import websockets

NULL = "\x00"

class StompClient:
    def __init__(self, url, token):
        self.url = url
        self.token = token
        self.ws = None
        self.connected = asyncio.Event()
        self.handlers = {}
        self._rx_task = None
        self._hb_task = None
        self._last_rx = 0

    @staticmethod
    def frame(command, headers=None, body=""):
        headers = headers or {}
        lines = [command]
        for key, value in headers.items():
            lines.append(f"{key}:{value}")
        return "\n".join(lines) + "\n\n" + body + NULL

    async def connect(self):
        self.ws = await asyncio.wait_for(
            websockets.connect(
                self.url,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=2,
                max_size=4 * 1024 * 1024,
            ),
            timeout=8,
        )
        await self.ws.send(self.frame(
            "CONNECT",
            {
                "Authorization": f"Bearer {self.token}",
                "accept-version": "1.2",
                "heart-beat": "4000,4000",
            },
        ))
        self._rx_task = asyncio.create_task(self._receive_loop())

        for _ in range(80):
            if self.connected.is_set():
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("STOMP CONNECT timeout")

    async def _receive_loop(self):
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                self._last_rx = asyncio.get_running_loop().time()

                for frame in self._split_frames(raw):
                    await self._dispatch(frame)
        except Exception as exc:
            if self.connected.is_set():
                self.connected.clear()
            print(f"[STOMP] receive loop ended: {exc}")

    @staticmethod
    def _split_frames(raw):
        raw = raw.replace("\r\n", "\n")
        return [x for x in raw.split(NULL) if x.strip()]

    async def _dispatch(self, raw):
        lines = raw.split("\n")
        command = lines[0].strip()
        i = 1
        headers = {}
        while i < len(lines) and lines[i] != "":
            if ":" in lines[i]:
                k, v = lines[i].split(":", 1)
                headers[k] = v
            i += 1
        body = "\n".join(lines[i + 1:]) if i < len(lines) else ""

        if command == "CONNECTED":
            self.connected.set()
            self._hb_task = asyncio.create_task(self._heartbeat())
            return

        if command == "MESSAGE":
            destination = headers.get("destination", "")
            handler = self.handlers.get(destination)
            if handler:
                try:
                    await handler(body, headers)
                except Exception as exc:
                    print(f"[STOMP] handler error for {destination}: {exc}")
            return

        if command == "ERROR":
            print(f"[STOMP] ERROR headers={headers} body={body}")

    async def _heartbeat(self):
        while self.connected.is_set():
            await asyncio.sleep(4)
            try:
                await self.ws.send("\n")
            except Exception:
                return

    async def subscribe(self, destination, handler, sub_id):
        self.handlers[destination] = handler
        await self.ws.send(self.frame(
            "SUBSCRIBE",
            {"id": sub_id, "destination": destination, "ack": "auto"},
        ))

    async def send(self, destination, payload):
        await self.ws.send(self.frame(
            "SEND",
            {
                "destination": destination,
                "content-type": "application/json",
            },
            json.dumps(payload, separators=(",", ":")),
        ))

    async def close(self):
        self.connected.clear()
        if self._hb_task:
            self._hb_task.cancel()
        if self._rx_task:
            self._rx_task.cancel()
        if self.ws:
            try:
                await self.ws.close(code=1000)
            except Exception:
                pass
