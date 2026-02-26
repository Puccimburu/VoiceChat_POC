import asyncio
import logging
import queue as queue_mod
import threading
import time
from typing import Optional

from google.cloud import speech as gcp_speech

logger = logging.getLogger("ws_gateway")

_STT_MAX_RETRIES = 1
_STT_RETRY_DELAY = 0.2  # seconds before retry


class STTSession:
    """Google Cloud Speech streaming session running in a background thread."""

    def __init__(self):
        self._audio_q:   queue_mod.Queue = queue_mod.Queue(maxsize=400)
        self._audio_buf: list            = []   # full copy kept for retry replay
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
        self._audio_buf.append(data)   # always buffer for potential retry
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

    def _retry_generator(self):
        """Replay the complete audio buffer for a retry attempt."""
        for chunk in list(self._audio_buf):   # snapshot — thread-safe copy
            if self._stop.is_set():
                return
            yield gcp_speech.StreamingRecognizeRequest(audio_content=chunk)

    def _make_stt_config(self):
        return gcp_speech.StreamingRecognitionConfig(
            config=gcp_speech.RecognitionConfig(
                encoding=gcp_speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=48000,
                language_code="en-US",
                enable_automatic_punctuation=True,
                model="latest_long",
            ),
            single_utterance=True,
        )

    def _run(self):
        transcript = ""
        attempt    = 0

        while attempt <= _STT_MAX_RETRIES and not self._stop.is_set():
            try:
                gen = self._audio_generator() if attempt == 0 else self._retry_generator()
                for resp in gcp_speech.SpeechClient().streaming_recognize(
                    self._make_stt_config(), gen
                ):
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
                break  # no exception — done

            except Exception as e:
                if self._stop.is_set():
                    break
                logger.error(f"[STT] error (attempt {attempt + 1}): {e}")
                attempt += 1
                if attempt <= _STT_MAX_RETRIES:
                    logger.info(
                        f"[STT] transient error — retrying in {_STT_RETRY_DELAY}s "
                        f"({len(self._audio_buf)} chunks buffered)"
                    )
                    time.sleep(_STT_RETRY_DELAY)
                else:
                    logger.error("[STT] giving up after retry")
                    self._stop.set()

        if self._loop and self._transcript_future and not self._transcript_future.done():
            self._loop.call_soon_threadsafe(
                self._transcript_future.set_result, transcript
            )
