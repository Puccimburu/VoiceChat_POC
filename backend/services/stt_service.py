"""Speech-to-Text service"""
import logging
from google.cloud import speech
from config import STT_ENCODING, STT_SAMPLE_RATE, STT_LANGUAGE, STT_MODEL, CHUNK_SIZE

logger = logging.getLogger(__name__)

# Initialize Google Cloud Speech client
speech_client = speech.SpeechClient()


def transcribe_audio(audio_bytes):
    """Transcribe audio using Google Cloud Speech-to-Text streaming API

    Args:
        audio_bytes: Audio data in bytes

    Returns:
        str: Transcribed text
    """
    # Configure STT
    config = speech.RecognitionConfig(
        encoding=getattr(speech.RecognitionConfig.AudioEncoding, STT_ENCODING),
        sample_rate_hertz=STT_SAMPLE_RATE,
        language_code=STT_LANGUAGE,
        enable_automatic_punctuation=True,
        model=STT_MODEL
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=config,
        single_utterance=False,
        interim_results=True  # Enable interim results as fallback
    )

    def request_generator():
        """Generator that yields audio chunks"""
        for i in range(0, len(audio_bytes), CHUNK_SIZE):
            yield speech.StreamingRecognizeRequest(
                audio_content=audio_bytes[i:i + CHUNK_SIZE]
            )

    # Process streaming recognition
    user_message = ""
    try:
        responses = speech_client.streaming_recognize(streaming_config, request_generator())

        # Collect transcripts
        final_transcripts = []
        last_transcript = ""

        for response in responses:
            for result in response.results:
                if result.alternatives:
                    transcript = result.alternatives[0].transcript
                    if result.is_final:
                        final_transcripts.append(transcript)
                    else:
                        last_transcript = transcript

        # Prefer final results, fallback to last interim if no finals
        if final_transcripts:
            user_message = " ".join(final_transcripts).strip()
        elif last_transcript:
            user_message = last_transcript.strip()
            logger.info("  Using interim result (no finals received)")

    except Exception as e:
        logger.error(f" Streaming recognition error: {e}", exc_info=True)
        user_message = ""

    return user_message
