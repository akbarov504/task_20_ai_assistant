import asyncio
import json

from aiortc import (
    RTCPeerConnection,
    RTCConfiguration,
    RTCIceServer,
    RTCSessionDescription,
)
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

from .audio import GeminiAudioTrack, driver_track_to_pcm16

class WebRTCClient:
    def __init__(self, ice_config, stomp, gemini, recorder=None, on_failed=None):
        servers = []
        self._greeting_sent = False
        self.recorder = recorder
        self.on_failed = on_failed
        self._failed_reported = False

        for item in ice_config.get("iceServers", []):
            urls = item.get("urls")

            if not urls:
                continue

            servers.append(
                RTCIceServer(
                    urls=urls,
                    username=item.get("username"),
                    credential=item.get("credential"),
                )
            )

        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
        self.stomp = stomp
        self.gemini = gemini
        self.call_id = None
        self.stop_event = asyncio.Event()
        self.gemini_track = GeminiAudioTrack(
            on_frame=recorder.record_gemini_pcm24 if recorder else None
        )
        self._audio_task = None
        self._gemini_output_task = None
        self.pc.addTrack(self.gemini_track)
        self.gemini.on_interrupted = self.gemini_track.clear

        @self.pc.on("track")
        def on_track(track):
            print(f"[WEBRTC] remote track: {track.kind}")

            if track.kind == "audio":
                print("[WEBRTC] DRIVER AUDIO TRACK RECEIVED")

                self._audio_task = asyncio.create_task(
                    driver_track_to_pcm16(
                        track,
                        self.gemini,
                        self.stop_event,
                        self.recorder,
                    )
                )

        @self.pc.on("connectionstatechange")
        async def on_connection_state():
            state = self.pc.connectionState
            print(f"[WEBRTC] Connection state: {state}")

            if (
                state == "connected"
                and not self._greeting_sent
                and not self.stop_event.is_set()
            ):
                self._greeting_sent = True
                self.start_gemini_audio_bridge()

                try:
                    await self.gemini.start_conversation()
                except Exception as exc:
                    print(f"[GEMINI] Failed to start conversation: {exc}")

            elif state == "failed":
                await self._report_failure("WEBRTC_FAILED")

        @self.pc.on("iceconnectionstatechange")
        async def on_ice_state():
            state = self.pc.iceConnectionState
            print(f"[WEBRTC] ICE state: {state}")

            if state in ("connected", "completed"):
                print("[WEBRTC] MEDIA READY")
                if not self._greeting_sent and not self.stop_event.is_set():
                    self._greeting_sent = True
                    self.start_gemini_audio_bridge()
                    try:
                        await self.gemini.start_conversation()
                    except Exception as exc:
                        print(f"[GEMINI] Failed to start conversation: {exc}")

            elif state == "checking":
                print("[WEBRTC] ICE checking...")

            elif state == "failed":
                print("[WEBRTC] ICE FAILED")
                await self._report_failure("ICE_FAILED")

            elif state == "disconnected":
                print("[WEBRTC] ICE DISCONNECTED")

            elif state == "closed":
                print("[WEBRTC] ICE CLOSED")

        @self.pc.on("icecandidate")
        async def on_ice_candidate(candidate):
            if not candidate:
                return

            if not self.call_id:
                print("[WEBRTC] ICE candidate skipped: no call_id")
                return

            if not self.stomp.connected.is_set():
                print("[WEBRTC] ICE candidate skipped: STOMP not connected")
                return

            try:
                await self.stomp.send(
                    "/app/calls.ice",
                    {
                        "callId": self.call_id,
                        "candidate": "candidate:" + candidate_to_sdp(candidate),
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    },
                )

                print("[WEBRTC] ICE candidate sent")

            except Exception as exc:
                print(f"[WEBRTC] Failed to send ICE candidate: {exc}")

    async def _report_failure(self, reason):
        self.stop_event.set()
        if self._failed_reported:
            return
        self._failed_reported = True
        if self.on_failed is not None:
            try:
                await self.on_failed(reason)
            except Exception as exc:
                print(f"[WEBRTC] on_failed error: {exc}")

    async def _gemini_to_webrtc(self):
        print("[AUDIO] Gemini -> WebRTC bridge started")

        while not self.stop_event.is_set():
            try:
                pcm24 = await self.gemini.receive_audio()

                if not pcm24:
                    continue

                await self.gemini_track.push_pcm24k(pcm24)

            except asyncio.CancelledError:
                print("[AUDIO] Gemini -> WebRTC bridge cancelled")
                break

            except Exception as exc:
                print(f"[AUDIO] Gemini -> WebRTC bridge error: {exc}")
                break

        print("[AUDIO] Gemini -> WebRTC bridge stopped")

    def start_gemini_audio_bridge(self):
        if self._gemini_output_task is None:
            self._gemini_output_task = asyncio.create_task(
                self._gemini_to_webrtc()
            )

            print("[AUDIO] Gemini audio bridge task created")

    async def create_and_send_offer(self):
        self.start_gemini_audio_bridge()

        print("[WEBRTC] Creating SDP offer...")
        offer = await self.pc.createOffer()

        await self.pc.setLocalDescription(offer)
        print("[WEBRTC] Local SDP created")

        await self.stomp.send(
            "/app/calls.offer",
            {
                "callId": self.call_id,
                "sdp": self.pc.localDescription.sdp,
            },
        )
        print("[WEBRTC] SDP offer sent")

    async def handle_signal(self, body):
        try:
            envelope = json.loads(body)
        except Exception as exc:
            print(f"[WEBRTC] Invalid signaling JSON: {exc}")
            return

        nested = envelope.get("payload")
        msg_type = envelope.get("type")
        call_id = envelope.get("callId")

        if nested:
            payload = nested

            if not call_id:
                call_id = payload.get("callId")

        else:
            payload = envelope

        if call_id and not self.call_id:
            self.call_id = call_id
            print(f"[CALL] callId={self.call_id}")

        sdp = payload.get("sdp")

        if sdp:
            if msg_type == "answer":
                try:
                    await self.pc.setRemoteDescription(
                        RTCSessionDescription(sdp=sdp, type="answer")
                    )
                    print("[WEBRTC] remote SDP answer set")

                except Exception as exc:
                    print(f"[WEBRTC] Failed to set remote answer: {exc}")
                return

            is_offer = (
                msg_type == "offer"
                or (
                    sdp.startswith("v=0")
                    and "a=setup:actpass" in sdp
                )
            )

            if is_offer:
                try:
                    print("[WEBRTC] Remote SDP offer received")

                    await self.pc.setRemoteDescription(
                        RTCSessionDescription(
                            sdp=sdp,
                            type="offer",
                        )
                    )

                    answer = await self.pc.createAnswer()
                    await self.pc.setLocalDescription(answer)

                    await self.stomp.send(
                        "/app/calls.answer",
                        {
                            "callId": self.call_id,
                            "sdp": self.pc.localDescription.sdp,
                        },
                    )
                    print("[WEBRTC] SDP answer sent")

                except Exception as exc:
                    print(f"[WEBRTC] Failed to handle remote offer: {exc}")
                return

        candidate = payload.get("candidate")
        if candidate:
            try:
                cand = candidate.strip().removeprefix("a=").removeprefix("candidate:")
                c = candidate_from_sdp(cand)

                c.sdpMid = payload.get("sdpMid")
                c.sdpMLineIndex = payload.get("sdpMLineIndex")

                await self.pc.addIceCandidate(c)
                print("[WEBRTC] Remote ICE candidate added")

            except Exception as exc:
                print(f"[WEBRTC] ICE candidate ignored: {exc}")

    async def close(self):
        print("[WEBRTC] Closing...")

        self.stop_event.set()
        if self._audio_task:
            self._audio_task.cancel()

            try:
                await self._audio_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if self._gemini_output_task:
            self._gemini_output_task.cancel()

            try:
                await self._gemini_output_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        try:
            await self.pc.close()
        except Exception:
            pass

        print("[WEBRTC] Closed")
