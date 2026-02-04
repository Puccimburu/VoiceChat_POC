"""Speech-to-Text service with retry logic"""
import logging
import time
import os
from google.cloud import speech
from config import STT_LANGUAGE, STT_MODEL

# Workaround for SSL/gRPC issues on Python 3.14
os.environ.setdefault('GRPC_ENABLE_FORK_SUPPORT', '0')
os.environ.setdefault('GRPC_POLL_STRATEGY', 'poll')

logger = logging.getLogger(__name__)

# Global client - recreated on connection errors
_speech_client = None


def get_speech_client(force_new=False):
    """Get or create speech client"""
    global _speech_client
    if _speech_client is None or force_new:
        _speech_client = speech.SpeechClient()
    return _speech_client


def transcribe_audio(audio_bytes, encoding=None):
    """Transcribe audio using Google Cloud Speech-to-Text synchronous API with retry

    Args:
        audio_bytes: Audio data in bytes (webm/opus or ogg/opus format)
        encoding: Optional AudioEncoding enum value (defaults to WEBM_OPUS)

    Returns:
        str: Transcribed text
    """
    if encoding is None:
        encoding = speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
    max_retries = 3
    retry_count = 0

    while retry_count <= max_retries:
        try:
            # Use fresh client on retry to avoid stale connections
            client = get_speech_client(force_new=(retry_count > 0))

            # Configure for audio from browser MediaRecorder
            config = speech.RecognitionConfig(
                encoding=encoding,
                sample_rate_hertz=48000,
                language_code=STT_LANGUAGE,
                enable_automatic_punctuation=True,
                model=STT_MODEL  # latest_long works well with opus codecs
            )

            # Create audio object
            audio = speech.RecognitionAudio(content=audio_bytes)

            start_time = time.time()

            # Use synchronous recognize for complete audio files
            response = client.recognize(config=config, audio=audio)

            elapsed = time.time() - start_time

            # Extract transcript from response
            if response.results:
                transcript = " ".join([result.alternatives[0].transcript for result in response.results]).strip()
                if transcript:
                    logger.info(f" STT completed in {elapsed:.3f}s")
                    return transcript

            logger.warning(f" Empty transcript ({elapsed:.3f}s)")
            return ""

        except Exception as e:
            error_str = str(e).lower()

            # Check if it's a transient SSL/gRPC error
            is_transient = any(x in error_str for x in [
                'stream removed', 'ssl', 'corrupt', 'reset', 'unavailable',
                'deadline', 'cancelled', 'internal', 'bad_record_mac'
            ])

            if is_transient and retry_count < max_retries:
                retry_count += 1
                wait_time = 0.2 * retry_count
                logger.warning(f" STT error, retrying ({retry_count}/{max_retries}) in {wait_time}s: {type(e).__name__}")
                time.sleep(wait_time)
                continue

            logger.error(f" STT failed after {retry_count} retries: {e}")
            return ""

    return ""
