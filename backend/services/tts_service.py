"""Text-to-Speech service"""
import base64
import logging
from google.cloud import texttospeech_v1beta1 as texttospeech
from config import TTS_SAMPLE_RATE, TTS_SPEAKING_RATE, MALE_VOICES

logger = logging.getLogger(__name__)

# Initialize Google Cloud TTS client
tts_client = texttospeech.TextToSpeechClient()


def synthesize_sentence_with_timing(sentence, voice_name):
    """Generate TTS audio with word-level timing for a sentence

    Args:
        sentence: Text to synthesize
        voice_name: Google TTS voice (e.g. 'en-US-Neural2-J')

    Returns:
        tuple: (audio_base64, word_timing_data)
    """
    words = sentence.split()

    # Create SSML with word markers
    ssml_text = '<speak>'
    for i, word in enumerate(words):
        ssml_text += f'<mark name="word_{i}"/>{word} '
    ssml_text += '</speak>'

    # Determine gender from voice name
    gender = texttospeech.SsmlVoiceGender.MALE if voice_name in MALE_VOICES else texttospeech.SsmlVoiceGender.FEMALE

    # Configure TTS request
    synthesis_input = texttospeech.SynthesisInput(ssml=ssml_text)
    voice = texttospeech.VoiceSelectionParams(
        language_code="en-US",
        name=voice_name,
        ssml_gender=gender
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        sample_rate_hertz=TTS_SAMPLE_RATE,
        speaking_rate=TTS_SPEAKING_RATE
    )

    tts_request = texttospeech.SynthesizeSpeechRequest(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config,
        enable_time_pointing=[
            texttospeech.SynthesizeSpeechRequest.TimepointType.SSML_MARK
        ]
    )

    # Synthesize speech
    tts_response = tts_client.synthesize_speech(request=tts_request)

    # Encode audio to base64
    audio_base64 = base64.b64encode(tts_response.audio_content).decode('utf-8')

    # Extract word timing data
    word_timing_data = []
    for i, word in enumerate(words):
        time_seconds = 0
        for timepoint in tts_response.timepoints:
            if timepoint.mark_name == f'word_{i}':
                time_seconds = timepoint.time_seconds
                break
        word_timing_data.append({
            'word': word,
            'time_seconds': time_seconds
        })

    return audio_base64, word_timing_data
