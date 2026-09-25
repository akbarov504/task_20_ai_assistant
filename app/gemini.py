import asyncio
import base64
import json
import logging
import time

import websockets
from pydantic import ValidationError

from app.gemini_cost import GeminiCostCalculator, GeminiLiveSessionTokens, GeminiModel
from app.prompts import all_prompts
from app.tools import TOOL_DECLARATIONS, EndConversationArgs
from app.utils import close_call_by_cdl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GeminiLive:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ws = None
        self.output_queue = asyncio.Queue()
        self._rx_task = None
        self._tool_tasks = {}
        self._bg_tasks = set()
        self._seen_tool_ids = set()
        self.user_pause_react_time = 1
        self._last_activity_end = None
        self._end_conversation_has_been_called = False
        self._closing = False
        self._cost_logged = False
        self.on_interrupted = None
        self.on_end_requested = None
        self.on_disconnect = None

        try:
            model_enum = GeminiModel(self.cfg.gemini_model)
        except ValueError:
            model_enum = GeminiModel.FLASH_3_1_LIVE_PREVIEW
            logger.warning(
                f"Model '{self.cfg.gemini_model}' has no pricing entry, "
                f"cost will be estimated with {model_enum.value} rates."
            )
        self.token_session = GeminiLiveSessionTokens(model=model_enum)

    async def connect(self):
        if not self.cfg.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is empty")

        url = (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={self.cfg.gemini_api_key}"
        )

        self.ws = await websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,
        )

        generation_config = {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": self.cfg.gemini_voice
                    }
                }
            },
        }
        if self.cfg.gemini_model.startswith("gemini-3"):
            generation_config["thinkingConfig"] = {
                "thinkingLevel": "high"  # "low", "medium", "high"
            }
        prompt  = ""
        if self.cfg.payload_prompt_type=="FATIGUE_SYSTEM_PROMPT":
            prompt = all_prompts['FATIGUE_SYSTEM_PROMPT']

        elif self.cfg.payload_prompt_type=="DASH_CAM_EVENT_PROMPT" and self.cfg.ai_tone=='POLITE':
            prompt = all_prompts['DASH_CAM_EVENT_PROMPT_POLITE']

        elif self.cfg.payload_prompt_type=="DASH_CAM_EVENT_PROMPT" and self.cfg.ai_tone=='AGRESSIVE':
            prompt = all_prompts['DASH_CAM_EVENT_PROMPT_AGRESSIVE']

        setup = {
            "setup": {
                "model": f"models/{self.cfg.gemini_model}",
                "generationConfig": generation_config,
                "systemInstruction": {
                    "parts": [
                        {
                            "text": prompt
                        }
                    ]
                },
                "tools": [{
                    "functionDeclarations": TOOL_DECLARATIONS,
                }],
            }
        }

        await self.ws.send(json.dumps(setup))
        first = json.loads(await self.ws.recv())
        if "setupComplete" not in first:
            raise RuntimeError(f"Gemini setup failed: {first}")

        self._rx_task = asyncio.create_task(self._receive_loop())

    async def start_conversation(self):
        text = (
            "The driver is connected. Begin the call now "
            "with a brief greeting and one short question. "
            "Do not invent a reason for the call."
        )
        if self.cfg.payload :
            text = text + "\nSystem detected following event details:\n" + self.cfg.payload
        await self.ws.send(json.dumps({
            "clientContent": {
                "turns": [{
                    "role": "user",
                    "parts": [{
                        "text": text
                    }],
                }],
                "turnComplete": True,
            }
        }))

    async def _send_tool_response(self, call, result):
        await self.ws.send(json.dumps({
            "toolResponse": {
                "functionResponses": [{
                    "id": call["id"],
                    "name": call["name"],
                    "response": result,
                }],
            },
        }))

    async def close_call(self, args: EndConversationArgs, call):
        logger.info(f"Stop conversation args: {args}")
        stop_reason = args.reason
        result = {
            "status": "accepted",
            "message": "request accepted. Disconnecting.",
        }

        logger.info(f"Conversation {self.cfg.driver_cdl} is ending due to tool call: {stop_reason}")
        self.cost_calculator()
        await self._send_tool_response(call, result)
        await asyncio.sleep(5)

        if self.on_end_requested is not None:
            task = asyncio.create_task(self.on_end_requested(stop_reason))
        else:
            task = asyncio.create_task(close_call_by_cdl(self.cfg.driver_cdl, stop_reason))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        logger.info(f"call end requested for driver_cdl={self.cfg.driver_cdl} with reason={stop_reason}")

    async def _execute_tool(self, call):
        logger.info(f"Executing tool: {call}")
        name = call.get("name")
        arguments = call.get("args", {})

        try:
            try:
                if name == "end_conversation":
                    self._end_conversation_has_been_called = True
                    await self.close_call(EndConversationArgs.model_validate(arguments), call)

                else:
                    result = {
                        "status": "error",
                        "message": "Unknown tool",
                    }
                    await self._send_tool_response(call, result)

            except ValidationError as exc:
                logger.error(f"Tool {name} invalid arguments: {exc}")
                self._end_conversation_has_been_called = False
                result = {
                    "status": "error",
                    "message": (
                        "Invalid arguments. Supply the required "
                        "nonempty strings and no extra fields."
                    ),
                }
                await self._send_tool_response(call, result)

            except asyncio.TimeoutError:
                result = {
                    "status": "error",
                    "message": (
                        "Report timed out. Completion is unconfirmed; "
                        "do not claim the safety team was notified."
                    ),
                }
                await self._send_tool_response(call, result)

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.error(f"Tool {name} failed: {exc}")
                result = {
                    "status": "error",
                    "message": "Tool execution failed",
                }
                await self._send_tool_response(call, result)

        except asyncio.CancelledError as exp:
            logger.error("CancelledError : " + str(exp))
            raise

        except Exception as exc:
            logger.error(f"Tool response or shutdown failed: {exc}")

        finally:
            self._tool_tasks.pop(call["id"], None)

    async def _catch_user_pause(self, msg):
        try:

            if 'voiceActivity' in msg:
                self._last_activity_end = time.monotonic()
                return
            if "serverContent" in msg:
                if 'generationComplete' in msg['serverContent']:
                    if msg["serverContent"]["generationComplete"]:
                        self._last_activity_end = time.monotonic()
                        return

            if self._last_activity_end:
                delta_time = time.monotonic() - self._last_activity_end
                logger.info(f"[Gemini] delta_time={delta_time}, last_activity_end={self._last_activity_end}, now={time.monotonic()}")
                if delta_time > self.cfg.user_pause_react_time:
                    logger.info(f"[Gemini] User has not responded in {delta_time} sec. Sending message to Gemini.")
                    await self.ws.send(json.dumps({
                        "clientContent": {
                            "turns": [{
                                "role": "user",
                                "parts": [{
                                    "text": "user has not responded in " + str(delta_time) + " seconds "
                                }],
                            }],
                            "turnComplete": True,
                        }
                    }))
                    self._last_activity_end = time.monotonic()

        except Exception as exc:
            logger.error(f"[GeminiERROR] _catch_user_pause error: {exc}")

    def cost_calculator(self):
        if self._cost_logged:
            return None
        try:
            calculator = GeminiCostCalculator()
            session_cost = calculator.calculate_session_cost(self.token_session)
            self._cost_logged = True
            logger.info(f"--- Model: {session_cost['model_used']} (CDL={self.cfg.driver_cdl}) ---")
            logger.info(f"Grand Total USD: ${session_cost['grand_total_cost_usd']:.6f}\n")
            return session_cost
        except Exception as exp:
            logger.error(f"GEMINI: Cost calculation exception: {exp}")

        return None

    def drain_output(self):
        while True:
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    @staticmethod
    def _log_message(msg):
        server = msg.get("serverContent")
        turn = server.get("modelTurn") if isinstance(server, dict) else None
        if not turn:
            logger.info(f"[Gemini] Received message: {msg}")
            return

        parts = []
        has_audio = False
        for part in turn.get("parts", []):
            inline = part.get("inlineData")
            if inline:
                has_audio = True
                parts.append({"inlineData": {
                    "mimeType": inline.get("mimeType"),
                    "data": f"<{len(inline.get('data', ''))} base64 chars>",
                }})
            else:
                parts.append(part)

        redacted = {**msg, "serverContent": {**server, "modelTurn": {**turn, "parts": parts}}}
        (logger.debug if has_audio else logger.info)(f"[Gemini] Received message: {redacted}")

    async def _receive_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)

                await self._catch_user_pause(msg)

                self._log_message(msg)
                if 'serverContent' in msg and 'modelTurn' in msg['serverContent']:
                    self._last_activity_end = time.monotonic()

                cancellation = msg.get("toolCallCancellation") or {}
                for call_id in cancellation.get("ids", []):
                    task = self._tool_tasks.get(call_id)
                    if task:
                        task.cancel()

                if "activityEnd" in msg:
                    logger.info(f"[Gemini] activityEnd received at {time.monotonic()}")
                    self._last_activity_end = time.monotonic()

                elif "activityStart" in msg:
                    logger.info(f"[Gemini] activityStart received at {time.monotonic()}")
                    self._last_activity_end = None

                tool_call = msg.get("toolCall") or None
                if tool_call:
                    logger.info(f"Tool calling: {tool_call}")
                    for call in tool_call.get("functionCalls", []):
                        call_id = call.get("id")
                        if not call_id or call_id in self._seen_tool_ids:
                            continue

                        self._seen_tool_ids.add(call_id)
                        self._tool_tasks[call_id] = asyncio.create_task(
                            self._execute_tool(call)
                        )
                if "usageMetadata" in msg:
                    self.token_session.add_turn(msg['usageMetadata'])

                server = msg.get("serverContent") or {}

                if server.get("interrupted"):
                    logger.info("[Gemini] interrupted by driver, dropping queued audio")
                    self.drain_output()
                    if self.on_interrupted is not None:
                        try:
                            self.on_interrupted()
                        except Exception as exc:
                            logger.error(f"on_interrupted error: {exc}")

                driver_text = (
                    server.get("inputTranscription") or {}
                ).get("text")
                if driver_text:
                    logger.info(f"Driver: {driver_text}")

                assistant_text = (
                    server.get("outputTranscription") or {}
                ).get("text")
                if assistant_text:
                    logger.info(f"Assistant: {assistant_text}")

                turn = server.get("modelTurn") or {}

                for part in turn.get("parts", []):
                    inline = part.get("inlineData")

                    if not inline:
                        continue

                    mime = inline.get("mimeType", "")
                    if "audio/pcm" not in mime:
                        continue

                    data = inline.get("data")
                    if not data:
                        continue

                    try:
                        audio = base64.b64decode(data)
                    except Exception as exc:
                        logger.error(f"Base64 decode error: {exc}")
                        continue

                    if not audio:
                        continue

                    if len(audio) % 2 != 0:
                        logger.warning(f"Odd PCM chunk size={len(audio)}")

                    if not self._end_conversation_has_been_called:
                        await self.output_queue.put(audio)

        except asyncio.CancelledError:
            logger.info("Receive loop cancelled")
            return

        except Exception as exc:
            logger.error(f"Receive loop ended: {exc}")
            if self.ws:
                try:
                    logger.info(f'[GEMINI] call is ending due to {exc} error')
                    await self.ws.close()
                except Exception as close_exc:
                    logger.debug(f"Failed to close Gemini WebSocket: {close_exc}")

        if not self._closing and self.on_disconnect is not None:
            logger.warning(f"Gemini connection lost (CDL={self.cfg.driver_cdl}), ending call")
            try:
                await self.on_disconnect()
            except Exception as exc:
                logger.error(f"on_disconnect error: {exc}")

    async def send_pcm16(self, pcm16: bytes, sample_rate=16000):
        if not pcm16 or not self.ws:
            return

        await self.ws.send(json.dumps({
            "realtimeInput": {
                "audio": {
                    "mimeType": f"audio/pcm;rate={sample_rate}",
                    "data": base64.b64encode(pcm16).decode(),
                }
            }
        }))

    async def receive_audio(self):
        return await self.output_queue.get()

    async def close(self):
        self._closing = True
        self.cost_calculator()

        tasks = list(self._tool_tasks.values())
        if self._rx_task:
            tasks.append(self._rx_task)

        current = asyncio.current_task()
        tasks = [task for task in tasks if task is not current]

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._tool_tasks.clear()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
