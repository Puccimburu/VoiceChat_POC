"""Real-time streaming Speech-to-Text service"""
import os
import logging
import time
import queue
import threading
from google.cloud import speech
from config import STT_LANGUAGE, STT_MODEL


# gRPC environment workarounds for SSL/threading issues
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")
os.environ.setdefault("GRPC_POLL_STRATEGY", "poll")


logger = logging.getLogger(__name__)


# Thread-safe client management
_speech_client = None
_client_lock = threading.Lock()
_last_stream_had_error = False  # Track if previous stream failed


# Transient error patterns that indicate we should recreate the client
TRANSIENT_ERROR_PATTERNS = [
    "stream removed",
    "ssl",
    "corrupt",
    "reset",
    "unavailable",
    "deadline",
    "cancelled",
    "internal",
    "bad_record_mac",
    "tsi_data_corrupted",
]


def get_speech_client(force_new=False):
    """Get or create speech client with thread-safe access."""
    global _speech_client
    with _client_lock:
        if _speech_client is None or force_new:
            if force_new:
                logger.info("Recreating speech client due to previous error")
            _speech_client = speech.SpeechClient()
        return _speech_client


def _is_transient_error(error):
    """Check if an error is transient and warrants client recreation."""
    error_str = str(error).lower()
    return any(pattern in error_str for pattern in TRANSIENT_ERROR_PATTERNS)


def last_stream_had_error():
    """Return whether the last streaming STT session ended with an error."""
    return _last_stream_had_error


class StreamingSTT:
    """Manages a single real-time streaming STT session."""

    def __init__(self, encoding=None, sample_rate=48000):
        self.encoding = encoding or speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
        self.sample_rate = sample_rate
        self.audio_queue = queue.Queue()
        self.is_active = True
        self.final_transcript = ""
        self._complete_event = threading.Event()
        self._audio_buffer = bytearray()
        self.error = None  # Track errors for caller inspection
        self._started = False  # Prevent double start

    def add_audio_chunk(self, audio_bytes):
        if self.is_active:
            self.audio_queue.put(audio_bytes)

    def close(self):
        if not self.is_active:
            return
        self.is_active = False
        self.audio_queue.put(None)  # sentinel stops the generator

    def start(self):
        if self._started:
            return
        self._started = True
        t = threading.Thread(target=self._process_stream, daemon=True)
        t.start()

    def wait_for_result(self, timeout=5.0):
        self._complete_event.wait(timeout=timeout)
        return self.final_transcript.strip()

    def _audio_generator(self):
        while True:
            try:
                chunk = self.audio_queue.get(timeout=5.0)
                if chunk is None:
                    break
                self._audio_buffer.extend(chunk)
                if len(chunk) > 0:
                    yield speech.StreamingRecognizeRequest(audio_content=chunk)
            except queue.Empty:
                logger.info("No audio received for 5s - closing stream")
                break
            except Exception as e:
                logger.error(f"Error in audio generator: {e}")
                break

    def _process_stream(self):
        global _last_stream_had_error
        stream_start = time.time()

        try:
            # Use fresh client if previous stream had an error
            client = get_speech_client(force_new=_last_stream_had_error)

            streaming_config = speech.StreamingRecognitionConfig(
                config=speech.RecognitionConfig(
                    encoding=self.encoding,
                    sample_rate_hertz=self.sample_rate,
                    language_code=STT_LANGUAGE,
                    enable_automatic_punctuation=True,
                    model=STT_MODEL,
                ),
                single_utterance=True,
                interim_results=True,
            )

            logger.info(
                f"Starting streaming recognition (encoding: {self.encoding}, "
                f"rate: {self.sample_rate})"
            )

            responses = client.streaming_recognize(
                streaming_config,
                self._audio_generator(),
            )

            for response in responses:
                for result in response.results:
                    if not result.alternatives:
                        continue
                    transcript = result.alternatives[0].transcript
                    if result.is_final:
                        logger.info(f"Final transcript: {transcript}")
                        self.final_transcript += transcript + " "
                    else:
                        logger.debug(f"Interim: {transcript}")

            logger.info(f"Complete transcription: '{self.final_transcript.strip()}'")
            logger.info(
                f"Streaming STT duration: {time.time() - stream_start:.2f}s, "
                f"buffer size: {len(self._audio_buffer)} bytes"
            )

            # Success
            self.error = None
            _last_stream_had_error = False

        except Exception as e:
            logger.error(f"Streaming STT error: {e}")
            self.error = e
            # Mark for client recreation on next stream
            if _is_transient_error(e):
                _last_stream_had_error = True

        finally:
            self._complete_event.set()
