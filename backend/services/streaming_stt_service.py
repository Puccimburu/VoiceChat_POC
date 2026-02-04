"""Real-time streaming Speech-to-Text service"""
import logging
import time
import queue
import threading
from google.cloud import speech
from config import STT_LANGUAGE, STT_MODEL

logger = logging.getLogger(__name__)

speech_client = speech.SpeechClient()


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

    def add_audio_chunk(self, audio_bytes):
        if self.is_active:
            self.audio_queue.put(audio_bytes)

    def close(self):
        self.is_active = False
        self.audio_queue.put(None)  # sentinel stops the generator

    def start(self):
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
                logger.error(f" Error in audio generator: {e}")
                break

    def _process_stream(self):
        try:
            stream_start = time.time()

            streaming_config = speech.StreamingRecognitionConfig(
                config=speech.RecognitionConfig(
                    encoding=self.encoding,
                    sample_rate_hertz=self.sample_rate,
                    language_code=STT_LANGUAGE,
                    enable_automatic_punctuation=True,
                    model=STT_MODEL,
                ),
                single_utterance=False,
                interim_results=True,
            )

            logger.info(f" Starting streaming recognition (encoding: {self.encoding}, rate: {self.sample_rate})")

            responses = speech_client.streaming_recognize(
                streaming_config,
                self._audio_generator(),
            )


            for response in responses:
                for result in response.results:
                    if not result.alternatives:
                        continue
                    transcript = result.alternatives[0].transcript
                    if result.is_final:
                        logger.info(f" Final transcript: {transcript}")
                        self.final_transcript += transcript + " "
                    else:
                        logger.debug(f" Interim: {transcript}")

            logger.info(f" Complete transcription: '{self.final_transcript.strip()}'")
            logger.info(f"Streaming STT duration: {time.time() - stream_start:.2f}s, buffer size: {len(self._audio_buffer)} bytes")

        except Exception as e:
            logger.error(f" Streaming STT error: {e}", exc_info=True)
        finally:
            self._complete_event.set()
