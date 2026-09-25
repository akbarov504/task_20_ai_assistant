import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from .auth import CallAuth
from .ice import get_ice_config
from .stomp import StompClient
from .webrtc import WebRTCClient
from .gemini import GeminiLive
from .audio import CallRecorder
from .utils import register_client, unregister_client, get_active_clients, close_call_by_cdl
from app.service.cloud_service import get_cloud_service
from app.service.webhook_service import get_webhook_service
from app.analyse.analyse import analyse
from app.db.repositories import DBRepository

logger = logging.getLogger("client")

class CallClient:
    def __init__(self, cfg, auth=None):
        self.cfg = cfg
        self.auth = auth or CallAuth(cfg)
        self.stomp = None
        self.webrtc = None
        self.gemini = None
        self.call_id = None

        self.recording_id = self.cfg.call_id or f"{self.cfg.driver_cdl}_{int(time.time())}"
        self.recorder = CallRecorder(
            call_id=self.recording_id,
            recordings_dir=getattr(self.cfg, "recordings_dir", "app/utils/recordings"),
        )
        self.answer_event = asyncio.Event()
        self.end_event = asyncio.Event()
        self._ended = False
        self._close_task = None

        self.status = "INITIATED"
        self.end_reason = None
        self.driver_answered = False
        self.ringing_started_at: Optional[datetime] = None
        self.answered_at: Optional[datetime] = None
        self.ended_at: Optional[datetime] = None
        self.duration_seconds: float = 0.0
        self.talk_duration_seconds: float = 0.0

    async def start(self):
        if self.cfg.call_type not in ("DIRECT", "SPECIAL"):
            raise ValueError("CALL_TYPE must be DIRECT or SPECIAL")

        print("[1/6] Authenticating...")
        token = await self.auth.get_token()

        print("[2/6] Getting ICE config...")
        ice = await get_ice_config(self.cfg, token)

        print("[3/6] Connecting Gemini...")
        self.gemini = GeminiLive(self.cfg)
        self.gemini.on_end_requested = self.end
        self.gemini.on_disconnect = self._on_gemini_disconnect
        await self.gemini.connect()

        print("[4/6] Connecting STOMP...")
        self.stomp = StompClient(self.cfg.call_wss_url, token)

        await self.stomp.connect()
        await self.stomp.subscribe("/user/queue/call-events", self.on_call_event, "call-events")
        await self.stomp.subscribe("/user/queue/webrtc", self.on_webrtc, "webrtc")

        print("[5/6] Creating WebRTC...")
        self.webrtc = WebRTCClient(
            ice,
            self.stomp,
            self.gemini,
            recorder=self.recorder,
            on_failed=self.end,
        )

        print("[6/6] Calling driver...")
        self.status = "RINGING"
        self.ringing_started_at = datetime.now(timezone.utc)
        self.recorder.start()

        await DBRepository.update_call(
            call_id=self.recording_id,
            status="RINGING",
            ringing_started_at=self.ringing_started_at,
        )

        await self.stomp.send(
            "/app/calls.create-safetyops",
            {
                "driverCdl": self.cfg.driver_cdl,
                "callType": self.cfg.call_type,
            },
        )

        print(f"[CALL] ringing CDL={self.cfg.driver_cdl}, type={self.cfg.call_type}")

        answer_task = asyncio.create_task(self.answer_event.wait())
        end_task = asyncio.create_task(self.end_event.wait())

        try:
            done, pending = await asyncio.wait(
                {
                    answer_task,
                    end_task,
                },
                timeout=25,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if not done:
                print("[CALL] ring timeout")
                self.end_reason = "NO_ANSWER"
                await self.end("NO_ANSWER")
                return

            if self.answer_event.is_set():
                print("[CALL] driver answered")
                return

            if self.end_event.is_set():
                print("[CALL] call ended before answer")
                return

        finally:
            if not answer_task.done():
                answer_task.cancel()

            if not end_task.done():
                end_task.cancel()

    async def _on_gemini_disconnect(self):
        await self.end("GEMINI_DISCONNECTED")

    async def on_call_event(self, body, headers):
        try:
            msg = json.loads(body)
        except Exception as exc:
            print(f"[CALL EVENT] invalid JSON: {exc}")
            return

        call_id = msg.get("callId")

        if call_id and not self.call_id:
            self.call_id = call_id
            if self.webrtc:
                self.webrtc.call_id = call_id

            await DBRepository.update_call(
                call_id=self.recording_id,
                server_call_id=call_id,
            )

        event = msg.get("eventType")
        status = msg.get("status")

        print(f"[CALL EVENT] eventType={event} status={status} callId={call_id}")

        if event == "CALL_ACCEPTED" or status == "ANSWERED":
            print("[CALL] ANSWERED")
            self.status = "ANSWERED"
            self.driver_answered = True
            self.answered_at = datetime.now(timezone.utc)
            self.answer_event.set()

            await DBRepository.update_call(
                call_id=self.recording_id,
                status="ANSWERED",
                driver_answered=True,
                answered_at=self.answered_at,
            )

            if self.webrtc:
                self.webrtc.call_id = self.call_id
                try:
                    await self.webrtc.create_and_send_offer()
                except Exception as exc:
                    print(f"[CALL] Failed to create WebRTC offer: {exc}")
                    await self.end("WEBRTC_ERROR")
            return

        if status == "UNREACHABLE":
            print("[CALL] UNREACHABLE")
            self.status = "UNREACHABLE"
            self.end_reason = "UNREACHABLE"
            await self.end("UNREACHABLE")
            return

        if event == "CALL_REJECTED" or status == "REJECTED":
            print("[CALL] REJECTED")
            self.status = "REJECTED"
            self.end_reason = "REJECTED"
            await self.end("REJECTED")
            return

        if event == "CALL_ENDED" or status == "ENDED":
            print("[CALL] ENDED")
            self.status = "ENDED"
            self.end_reason = self.end_reason or "ENDED"
            self.end_event.set()
            return

    async def on_webrtc(self, body, headers):
        if not self.webrtc:
            return
        try:
            await self.webrtc.handle_signal(body)
        except Exception as exc:
            print(f"[WEBRTC] Signaling error: {exc}")

    async def end(self, reason="ENDED"):
        if self._ended:
            return

        self._ended = True
        self.end_reason = self.end_reason or reason
        self.ended_at = datetime.now(timezone.utc)
        print(f"[CALL] Ending call: {reason}")

        try:
            if self.call_id and self.stomp and self.stomp.connected.is_set():
                await asyncio.wait_for(
                    self.stomp.send(
                        "/app/calls.end",
                        {
                            "callId": self.call_id,
                            "status": "ENDED",
                        },
                    ),
                    timeout=5,
                )
        except Exception as exc:
            print(f"[CALL] Failed to send end event: {exc}")
        finally:
            self.end_event.set()

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_impl())
        await asyncio.shield(self._close_task)

    async def _close_impl(self):
        print(f"[CALL:{self.cfg.driver_cdl}] Closing client...")

        try:
            await self.end()
        except Exception as exc:
            print(f"[CALL:{self.cfg.driver_cdl}] end() error: {exc}")

        if self.webrtc:
            try:
                await self.webrtc.close()
            except Exception as exc:
                print(f"[CALL:{self.cfg.driver_cdl}] WebRTC close error: {exc}")

        if self.gemini:
            try:
                await self.gemini.close()
            except Exception as exc:
                print(f"[CALL:{self.cfg.driver_cdl}] Gemini close error: {exc}")

        if self.stomp:
            try:
                await self.stomp.close()
            except Exception as exc:
                print(f"[CALL:{self.cfg.driver_cdl}] STOMP close error: {exc}")

        if not self.ended_at:
            self.ended_at = datetime.now(timezone.utc)

        if self.ringing_started_at and self.ended_at:
            self.duration_seconds = max(0.0, (self.ended_at - self.ringing_started_at).total_seconds())

        if self.answered_at and self.ended_at:
            self.talk_duration_seconds = max(0.0, (self.ended_at - self.answered_at).total_seconds())

        gemini_cost_usd = 0.0
        gemini_tokens = None
        if self.gemini and hasattr(self.gemini, "token_session") and self.gemini.token_session:
            try:
                from app.gemini_cost import GeminiCostCalculator
                calc = GeminiCostCalculator()
                cost_details = calc.calculate_session_cost(self.gemini.token_session)
                gemini_cost_usd = cost_details.get("grand_total_cost_usd", 0.0)
                gemini_tokens = {
                    "audio_input_tokens": sum(turn.audio_input_tokens for turn in self.gemini.token_session.turns),
                    "text_input_tokens": sum(turn.text_input_tokens for turn in self.gemini.token_session.turns),
                    "output_tokens": sum(turn.total_output_tokens for turn in self.gemini.token_session.turns),
                    "total_tokens": sum(turn.total_token_count for turn in self.gemini.token_session.turns),
                    "cost_details": cost_details,
                }
            except Exception as c_exc:
                print(f"[CALL] Cost calculation error: {c_exc}")

        output_path = None
        if self.recorder:
            try:
                output_path = await self.recorder.stop()
            except Exception as exc:
                print(f"[CALL:{self.cfg.driver_cdl}] Recorder stop error: {exc}")

        gcs_uri = None
        signed_url = None
        final_call_status = "COMPLETED" if self.driver_answered else (self.end_reason or "ENDED")

        if output_path and os.path.exists(output_path):
            print(f"[PIPELINE:{self.recording_id}] Step 1: Audio file saved at {output_path}")

            analysis_obj = None
            raw_analysis_dict = None
            try:
                print(f"[PIPELINE:{self.recording_id}] Step 2: Running AI voice analysis via provider='{self.cfg.analyse_provider}' model='{self.cfg.analyse_model}'...")
                analysis_resp = await analyse(
                    file_path=output_path,
                    provider=getattr(self.cfg, "analyse_provider", "gemini"),
                    model=getattr(self.cfg, "analyse_model", "gemini-3.7-flash"),
                    mime_type="audio/mpeg",
                )
                if analysis_resp and analysis_resp.answer:
                    analysis_obj = analysis_resp.answer
                    raw_analysis_dict = analysis_resp.model_dump()
                    print(f"[PIPELINE:{self.recording_id}] Step 2 completed: Analysis language='{analysis_obj.language}', summary='{analysis_obj.call_summary[:60]}...'")
            except Exception as a_exc:
                logger.error(f"[PIPELINE:{self.recording_id}] Analysis failed: {a_exc}", exc_info=True)

            try:
                print(f"[PIPELINE:{self.recording_id}] Step 3: Uploading audio to Google Cloud Storage...")
                gcs_uri, signed_url = await get_cloud_service().upload_audio(
                    local_path=output_path,
                    call_id=self.recording_id,
                    delete_local=True,
                    generate_signed=True,
                    signed_url_days=7,
                )
                print(f"[PIPELINE:{self.recording_id}] Step 3 & 4 completed: GCS URI={gcs_uri}")
            except Exception as u_exc:
                logger.error(f"[PIPELINE:{self.recording_id}] GCS upload failed: {u_exc}", exc_info=True)

            await DBRepository.update_call(
                call_id=self.recording_id,
                status=final_call_status,
                end_reason=self.end_reason,
                driver_answered=self.driver_answered,
                ringing_started_at=self.ringing_started_at,
                answered_at=self.answered_at,
                ended_at=self.ended_at,
                duration_seconds=self.duration_seconds,
                talk_duration_seconds=self.talk_duration_seconds,
                recording_file_name=f"{self.recording_id}.mp3",
                recording_gcs_uri=gcs_uri,
                recording_signed_url=signed_url,
                gemini_model=self.cfg.gemini_model,
                gemini_cost_usd=gemini_cost_usd,
                gemini_tokens=gemini_tokens,
            )

            webhook_sent = False
            webhook_status = None
            webhook_resp = None

            if analysis_obj:
                driver_tone = analysis_obj.ai_assistant_driver.tone_driver if analysis_obj.ai_assistant_driver else None
                assistant_tone = analysis_obj.ai_assistant_driver.tone_assitant if analysis_obj.ai_assistant_driver else None
                fatigue_confirmed = bool(analysis_obj.ai_assistant_driver and analysis_obj.ai_assistant_driver.fatigue)
                fatigue_reason = analysis_obj.ai_assistant_driver.fatigue.reason if fatigue_confirmed else None
                fatigue_evidence = analysis_obj.ai_assistant_driver.fatigue.evidence if fatigue_confirmed else None

                sentiment = analysis_obj.common.sentiment if analysis_obj.common else None
                conclusion = analysis_obj.common.conclusion if analysis_obj.common else None
                conflict_present = analysis_obj.common.conflict.present if (analysis_obj.common and analysis_obj.common.conflict) else False
                conflict_data = analysis_obj.common.conflict.model_dump() if (analysis_obj.common and analysis_obj.common.conflict) else None
                open_issues = analysis_obj.common.open_issues if analysis_obj.common else None
                solved_issues = analysis_obj.common.solved_issues if analysis_obj.common else None
                segments = [seg.model_dump() for seg in analysis_obj.common.segments] if (analysis_obj.common and analysis_obj.common.segments) else None

                try:
                    print(f"[PIPELINE:{self.recording_id}] Step 5: Dispatching analysis webhook with audio_url (Signed URL)...")
                    answer_dict = analysis_obj.model_dump() if analysis_obj else {}
                    webhook_sent, webhook_status, webhook_resp = await get_webhook_service().send_analysis_webhook(
                        call_id=self.recording_id,
                        audio_url=signed_url,
                        answer=answer_dict,
                        provider=getattr(self.cfg, "analyse_provider", "gemini"),
                        model=getattr(self.cfg, "analyse_model", "gemini-3.7-flash"),
                    )
                except Exception as w_exc:
                    logger.error(f"[PIPELINE:{self.recording_id}] Webhook dispatch error: {w_exc}")

                await DBRepository.create_call_analysis(
                    call_id=self.recording_id,
                    provider=getattr(self.cfg, "analyse_provider", "gemini"),
                    model=getattr(self.cfg, "analyse_model", "gemini-3.7-flash"),
                    is_voice_call_empty=analysis_obj.is_this_voice_call_empty,
                    language=analysis_obj.language,
                    call_summary=analysis_obj.call_summary,
                    driver_tone=driver_tone,
                    assistant_tone=assistant_tone,
                    fatigue_confirmed=fatigue_confirmed,
                    fatigue_reason=fatigue_reason,
                    fatigue_evidence=fatigue_evidence,
                    sentiment=sentiment,
                    conclusion=conclusion,
                    conflict_present=conflict_present,
                    conflict_data=conflict_data,
                    open_issues=open_issues,
                    solved_issues=solved_issues,
                    segments=segments,
                    raw_response=raw_analysis_dict,
                    webhook_sent=webhook_sent,
                    webhook_sent_at=datetime.now(timezone.utc) if webhook_sent else None,
                    webhook_status_code=webhook_status,
                    webhook_response=webhook_resp,
                )
        else:
            await DBRepository.update_call(
                call_id=self.recording_id,
                status=final_call_status,
                end_reason=self.end_reason or "NO_ANSWER",
                driver_answered=self.driver_answered,
                ringing_started_at=self.ringing_started_at,
                answered_at=self.answered_at,
                ended_at=self.ended_at,
                duration_seconds=self.duration_seconds,
                talk_duration_seconds=self.talk_duration_seconds,
                gemini_model=self.cfg.gemini_model,
                gemini_cost_usd=gemini_cost_usd,
                gemini_tokens=gemini_tokens,
            )

        print(f"[CALL:{self.cfg.driver_cdl}] Client closed cleanly.")

async def run_client(cfg, auth=None):
    client = CallClient(cfg, auth=auth)
    await register_client(client)

    try:
        await client.start()

        if client.answer_event.is_set() and not client.end_event.is_set():
            print(f"[CALL:{cfg.driver_cdl}] active")
            try:
                await asyncio.wait_for(client.end_event.wait(), timeout=20 * 60)
            except asyncio.TimeoutError:
                print(f"[CALL:{cfg.driver_cdl}] maximum demo duration reached")
                await client.end("MAX_DURATION")

    except Exception as exc:
        print(f"[CALL:{cfg.driver_cdl}] Unexpected error: {exc}")
        logger.error(f"[CALL:{cfg.driver_cdl}] Error in run_client: {exc}", exc_info=True)

    finally:
        await unregister_client(client)
        await client.close()
