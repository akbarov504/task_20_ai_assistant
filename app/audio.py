import asyncio
import os
import re
import time
from fractions import Fraction

import av
import numpy as np
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import AudioFrame
from av.audio.resampler import AudioResampler

from app.service.cloud_service import get_cloud_service

class GeminiAudioTrack(MediaStreamTrack):
    kind = "audio"

    INPUT_RATE = 24000
    FRAME_DURATION_MS = 20
    FRAME_SAMPLES = INPUT_RATE * FRAME_DURATION_MS // 1000
    FRAME_BYTES = FRAME_SAMPLES * 2

    def __init__(self, on_frame=None):
        super().__init__()
        self._pcm_buffer = bytearray()
        self._pts = 0
        self._start = None
        self._on_frame = on_frame

    async def push_pcm24k(self, pcm: bytes):
        if self.readyState != "live" or not pcm:
            return

        self._pcm_buffer.extend(pcm)

    def clear(self):
        self._pcm_buffer.clear()

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError

        loop = asyncio.get_running_loop()
        if self._start is None:
            self._start = loop.time()
        else:
            delay = self._start + self._pts / self.INPUT_RATE - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)

        if self.readyState != "live":
            raise MediaStreamError

        available = len(self._pcm_buffer) // 2 * 2
        take = min(available, self.FRAME_BYTES)
        pcm = bytes(self._pcm_buffer[:take])
        del self._pcm_buffer[:take]

        pcm += bytes(self.FRAME_BYTES - take)

        if self._on_frame is not None:
            try:
                self._on_frame(pcm)
            except Exception as exc:
                print(f"[AUDIO] on_frame callback error: {exc}")

        samples = np.frombuffer(pcm, dtype="<i2").astype(np.int16, copy=True)
        frame = AudioFrame.from_ndarray(
            samples.reshape(1, self.FRAME_SAMPLES),
            format="s16",
            layout="mono",
        )
        frame.sample_rate = self.INPUT_RATE
        frame.time_base = Fraction(1, self.INPUT_RATE)
        frame.pts = self._pts

        self._pts += self.FRAME_SAMPLES
        return frame

    def stop(self):
        super().stop()
        self._pcm_buffer.clear()

class _Channel:
    GAP_THRESHOLD_SEC = 0.08

    def __init__(self, rate: int):
        self.rate = rate
        self.buf = bytearray()
        self.samples = 0

    def write(self, pcm: bytes, now: float, end_aligned: bool):
        n = len(pcm) // 2
        if n == 0:
            return
        pcm = pcm[: n * 2]

        target = int(now * self.rate) - (n if end_aligned else 0)
        gap = target - self.samples
        if gap > int(self.GAP_THRESHOLD_SEC * self.rate):
            self.buf.extend(bytes(gap * 2))
            self.samples += gap

        self.buf.extend(pcm)
        self.samples += n

class CallRecorder:
    def __init__(
        self,
        call_id: str,
        recordings_dir: str = "app/utils/recordings",
        sample_rate: int = 24000,
        auto_id: bool = False,
    ):
        raw_id = (call_id or "").removesuffix(".mp3")
        self.call_id = self._sanitize(raw_id) or f"call_{int(time.time())}"
        self._auto_id = auto_id
        self.server_call_id = None
        self.recordings_dir = recordings_dir
        self.sample_rate = sample_rate
        self.start_time = None
        self.driver = _Channel(sample_rate)
        self.gemini = _Channel(sample_rate)
        self._closed = False
        self.saved_file_path = None
        self.gcs_uri = None

    @staticmethod
    def _sanitize(name: str) -> str:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name or "").lstrip(".")
        return name[:128]

    def set_call_id(self, call_id: str):
        self.server_call_id = call_id
        if self._auto_id and call_id:
            new_id = self._sanitize(call_id)
            if new_id:
                self.call_id = new_id

    def start(self):
        if self.start_time is None:
            self.start_time = time.monotonic()
            print(f"[RECORDER:{self.call_id}] Recording started (ringing/call start)")

    def record_driver_pcm24(self, pcm_24k: bytes):
        if self._closed or self.start_time is None or not pcm_24k:
            return
        try:
            self.driver.write(pcm_24k, time.monotonic() - self.start_time, end_aligned=True)
        except Exception as exc:
            print(f"[RECORDER:{self.call_id}] Error recording driver audio: {exc}")

    def record_gemini_pcm24(self, pcm_24k: bytes):
        if self._closed or self.start_time is None or not pcm_24k:
            return
        try:
            self.gemini.write(pcm_24k, time.monotonic() - self.start_time, end_aligned=False)
        except Exception as exc:
            print(f"[RECORDER:{self.call_id}] Error recording Gemini audio: {exc}")

    async def stop(self):
        if self._closed:
            return None
        self._closed = True

        if self.start_time is None:
            return None

        try:
            output_path = await asyncio.to_thread(self._save_to_mp3)
            self.saved_file_path = output_path
            return output_path
        except Exception as exc:
            print(f"[RECORDER:{self.call_id}] Failed to save MP3: {exc}")
            return None
        finally:
            self.driver = _Channel(self.sample_rate)
            self.gemini = _Channel(self.sample_rate)

    def _save_to_mp3(self):
        left = np.frombuffer(self.driver.buf, dtype="<i2")
        right = np.frombuffer(self.gemini.buf, dtype="<i2")
        total_len = max(len(left), len(right))
        if total_len == 0:
            print(f"[RECORDER:{self.call_id}] No audio captured, skipping MP3 save")
            return None

        os.makedirs(self.recordings_dir, exist_ok=True)
        output_path = os.path.join(self.recordings_dir, f"{self.call_id}.mp3")
        tmp_path = output_path + ".part"

        driver_padded = np.pad(left, (0, total_len - len(left)), mode="constant")
        gemini_padded = np.pad(right, (0, total_len - len(right)), mode="constant")
        mixed = driver_padded.astype(np.int32) + gemini_padded.astype(np.int32)
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)

        container = av.open(tmp_path, mode="w", format="mp3")
        try:
            stream = container.add_stream("mp3", rate=self.sample_rate)
            stream.bit_rate = 64000
            stream.layout = "mono"

            frame_size = 1152
            for start in range(0, total_len, frame_size):
                chunk = mixed[start : start + frame_size]
                if len(chunk) < frame_size:
                    chunk = np.pad(chunk, (0, frame_size - len(chunk)), mode="constant")

                frame = av.AudioFrame.from_ndarray(
                    chunk.reshape(1, -1), format="s16", layout="mono"
                )
                frame.sample_rate = self.sample_rate
                frame.time_base = Fraction(1, self.sample_rate)
                frame.pts = start

                for packet in stream.encode(frame):
                    container.mux(packet)

            for packet in stream.encode(None):
                container.mux(packet)
        except Exception:
            container.close()
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        container.close()

        os.replace(tmp_path, output_path)
        duration_sec = total_len / self.sample_rate
        print(
            f"[RECORDER:{self.call_id}] Audio saved -> {output_path} "
            f"({duration_sec:.2f}s, mono: 1 channel mixed)"
        )
        return output_path

async def driver_track_to_pcm16(track, gemini, stop_event, recorder=None):
    resampler = AudioResampler(
        format="s16",
        layout="mono",
        rate=16000,
    )

    rec_resampler = (
        AudioResampler(format="s16", layout="mono", rate=recorder.sample_rate)
        if recorder
        else None
    )

    try:
        while not stop_event.is_set():
            try:
                frame = await asyncio.wait_for(
                    track.recv(),
                    timeout=1,
                )
            except asyncio.TimeoutError:
                continue
            except MediaStreamError:
                break

            if rec_resampler is not None:
                for rec_out in rec_resampler.resample(frame):
                    rec_pcm = rec_out.to_ndarray().astype("<i2", copy=False).tobytes()
                    if rec_pcm:
                        recorder.record_driver_pcm24(rec_pcm)

            for out in resampler.resample(frame):
                pcm = out.to_ndarray().astype(
                    "<i2", copy=False
                ).tobytes()

                if pcm:
                    await gemini.send_pcm16(pcm, 16000)

    except asyncio.CancelledError as exp:
        print(f"[AUDIO] audio has been lost \n\n\n\n\n: {exp}")
        raise

    except Exception as exc:
        print(f"[AUDIO] driver->Gemini error: {exc}")

    finally:
        print("[AUDIO] driver->Gemini stopped")
