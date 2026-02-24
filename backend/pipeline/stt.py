import asyncio
import logging
import queue as queue_mod
import threading
from typing import Optional

from google.cloud import speech as gcp_speech

logger = logging.getLogger("ws_gateway")


class STTSession:
    """Google Cloud Speech streaming session running in a background thread."""

    def __init__(self):
        self._audio_q: queue_mod.Queue = queue_mod.Queue(maxsize=200)
        self._done  = threading.Event()
        self._stop  = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._transcript_future: Optional[asyncio.Future] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> asyncio.Future:
        self._loop = loop
        self._transcript_future = loop.create_future()
        threading.Thread(target=self._run, daemon=True).start()
        return self._transcript_future

    def add_audio(self, data: bytes):
        if self._stop.is_set() or self._done.is_set():
            return
        try:
            self._audio_q.put_nowait(data)
        except queue_mod.Full:
            logger.warning("[STT] audio buffer full — dropping chunk")

    def done(self):
        """Normal end-of-speech: drains buffered audio before closing stream."""
        self._done.set()

    def close(self):
        """Hard cancel (barge-in or replaced by new stream)."""
        self._stop.set()
        self._done.set()

    def _audio_generator(self):
        # Must yield StreamingRecognizeRequest objects (not raw bytes) — see MEMORY.md
        while not self._stop.is_set():
            if self._done.is_set():
                while True:
                    try:
                        data = self._audio_q.get_nowait()
                        yield gcp_speech.StreamingRecognizeRequest(audio_content=data)
                    except queue_mod.Empty:
                        return
            try:
                data = self._audio_q.get(timeout=0.05)
                yield gcp_speech.StreamingRecognizeRequest(audio_content=data)
            except queue_mod.Empty:
                continue

    def _run(self):
        transcript = ""
        try:
            client = gcp_speech.SpeechClient()
            config = gcp_speech.StreamingRecognitionConfig(
                config=gcp_speech.RecognitionConfig(
                    encoding=gcp_speech.RecognitionConfig.AudioEncoding.LINEAR16,
                    sample_rate_hertz=48000,
                    language_code="en-US",
                    enable_automatic_punctuation=True,
                    model="latest_long",
                ),
                single_utterance=True,
            )
            for resp in client.streaming_recognize(config, self._audio_generator()):
                if self._stop.is_set():
                    break
                for result in resp.results:
                    if result.is_final and result.alternatives:
                        transcript = result.alternatives[0].transcript
                        logger.info(f"[STT] final: {transcript!r}")
                        break
                else:
                    continue
                break
        except Exception as e:
            if not self._stop.is_set():
                logger.error(f"[STT] error: {e}")
            self._stop.set()
        finally:
            if self._loop and self._transcript_future and not self._transcript_future.done():
                self._loop.call_soon_threadsafe(
                    self._transcript_future.set_result, transcript
                )
