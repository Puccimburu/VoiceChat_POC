from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai
from google.cloud import speech
from google.cloud import texttospeech_v1beta1 as texttospeech
import os
import base64
import logging
import json
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor


# Load environment variables
load_dotenv()


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = Flask(__name__, static_folder='build', static_url_path='')
CORS(app)


# Track active streams for interrupt cancellation
active_streams = {}

# In-memory session storage (temporary context, no database)
sessions = {}
SESSION_TIMEOUT = 24 * 60 * 60  # 24 hours in seconds
MAX_HISTORY = 10  # Keep last 10 conversation exchanges


# Configure Gemini API
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel(
    'gemini-2.5-flash-lite',
    system_instruction="You are a helpful voice assistant. Always respond in English. Keep responses concise and conversational (2-3 sentences max unless asked for details). Do not use markdown formatting like asterisks, bold, or italics. Speak naturally as if in a conversation."
)


# Initialize Google Cloud clients
speech_client = speech.SpeechClient()
tts_client = texttospeech.TextToSpeechClient()


logger.info("Google Cloud STT and TTS clients initialized!")


def get_or_create_session(session_id=None):
    """Get existing session or create new one with context storage

    Args:
        session_id: Optional session ID from cookie/header

    Returns:
        tuple: (session_id, session_data)
    """
    now = datetime.now()

    # Clean up expired sessions
    expired_sessions = [
        sid for sid, data in sessions.items()
        if now - data['last_access'] > timedelta(seconds=SESSION_TIMEOUT)
    ]
    for sid in expired_sessions:
        del sessions[sid]
        logger.info(f"🧹 Cleaned up expired session {sid}")

    # Create new session if needed
    if not session_id or session_id not in sessions:
        session_id = str(uuid.uuid4())
        sessions[session_id] = {
            'history': deque(maxlen=MAX_HISTORY),  # Auto-removes oldest when full
            'variables': {},  # Custom key-value storage
            'created': now,
            'last_access': now
        }
        logger.info(f" Created new session {session_id}")
    else:
        sessions[session_id]['last_access'] = now

    return session_id, sessions[session_id]


def add_to_conversation_history(session_id, user_message, ai_response):
    """Add exchange to session history"""
    if session_id in sessions:
        sessions[session_id]['history'].append({
            'user': user_message,
            'assistant': ai_response,
            'timestamp': datetime.now().isoformat()
        })


def build_context_prompt(session_data, current_message):
    """Build Gemini prompt with conversation history

    Args:
        session_data: Session dictionary with history
        current_message: Current user message

    Returns:
        str: Formatted prompt with context
    """
    if not session_data['history']:
        return current_message

    # Build conversation history
    history_text = "Previous conversation:\n"
    for exchange in session_data['history']:
        history_text += f"User: {exchange['user']}\n"
        history_text += f"Assistant: {exchange['assistant']}\n\n"

    # Add custom variables if any
    if session_data['variables']:
        history_text += f"Context: {session_data['variables']}\n\n"

    history_text += f"Current question: {current_message}"
    return history_text


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
    male_voices = ['en-US-Neural2-A', 'en-US-Neural2-D', 'en-US-Neural2-I', 'en-US-Neural2-J']
    gender = texttospeech.SsmlVoiceGender.MALE if voice_name in male_voices else texttospeech.SsmlVoiceGender.FEMALE

    # Configure TTS request
    synthesis_input = texttospeech.SynthesisInput(ssml=ssml_text)
    voice = texttospeech.VoiceSelectionParams(
        language_code="en-US",
        name=voice_name,
        ssml_gender=gender
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        sample_rate_hertz=24000,
        speaking_rate=1.1
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


@app.route('/')
def serve():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/cancel/<stream_id>', methods=['POST'])
def cancel_stream(stream_id):
    """Cancel an active streaming response"""
    global active_streams
    if stream_id in active_streams:
        active_streams[stream_id] = True
        logger.info(f" Cancelled stream {stream_id}")
        return jsonify({'status': 'cancelled'})
    return jsonify({'status': 'not_found'}), 404


@app.route('/api/session', methods=['GET', 'DELETE'])
def manage_session():
    """View or reset conversation session"""
    session_id = request.cookies.get('session_id') or request.headers.get('X-Session-ID')

    if request.method == 'GET':
        # View session data
        if session_id and session_id in sessions:
            session_data = sessions[session_id]
            return jsonify({
                'session_id': session_id,
                'history_count': len(session_data['history']),
                'history': list(session_data['history']),
                'variables': session_data['variables'],
                'created': session_data['created'].isoformat(),
                'last_access': session_data['last_access'].isoformat()
            })
        return jsonify({'error': 'No active session'}), 404

    elif request.method == 'DELETE':
        # Reset session
        if session_id and session_id in sessions:
            del sessions[session_id]
            logger.info(f"  Deleted session {session_id}")
            return jsonify({'status': 'session_reset'})
        return jsonify({'error': 'No active session'}), 404


@app.route('/api/voice', methods=['POST'])
def voice_unified():
    """Unified endpoint: Audio → STT → Gemini → TTS (streaming)"""
    global active_streams
    stream_id = str(time.time())
    active_streams[stream_id] = False

    # Get or create session for conversation context
    session_id_from_request = request.cookies.get('session_id') or request.headers.get('X-Session-ID')
    session_id, session_data = get_or_create_session(session_id_from_request)

    try:
        # Get audio and voice preference
        audio_data = request.json['audio']
        selected_voice = request.json.get('voice', 'en-US-Neural2-J')


        logger.info(f"=== Starting unified voice pipeline (stream {stream_id}, session {session_id[:8]}) ===")
        pipeline_start = time.time()


        # Step 1: Speech-to-Text WITH Parallel Gemini Pre-warming
        logger.info("Step 1: Transcribing audio + pre-warming Gemini...")
        audio_bytes = base64.b64decode(audio_data.split(',')[1])

        audio_size_bytes = len(audio_bytes)
        MAX_BYTES = 800000
        logger.info(f"📊 Audio: {audio_size_bytes/1000:.0f}KB")
        if audio_size_bytes > MAX_BYTES:
            logger.warning(f"🚫 REJECTED: {audio_size_bytes/1000:.0f}KB > {MAX_BYTES/1000}KB")
            return jsonify({'error': 'Audio too long. Speak for 5s max.'}), 400
        assert audio_size_bytes <= MAX_BYTES, "Audio size check failed"

        audio = speech.RecognitionAudio(content=audio_bytes)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,
            language_code="en-US",
            enable_automatic_punctuation=True,
            model="latest_long"
        )

        # Parallel execution: STT + Gemini cache warm-up
        stt_start = time.time()

        def run_stt():
            return speech_client.recognize(config=config, audio=audio)

        def prewarm_gemini():
            # Tiny request to warm up Gemini connection
            try:
                gemini_model.generate_content("hi", stream=False)
            except:
                pass  # Ignore errors, this is just for warming

        with ThreadPoolExecutor(max_workers=2) as executor:
            stt_future = executor.submit(run_stt)
            warmup_future = executor.submit(prewarm_gemini)

            stt_response = stt_future.result()
            warmup_future.result()  # Wait for warmup to complete

        stt_time = time.time() - stt_start


        user_message = ""
        for result in stt_response.results:
            user_message += result.alternatives[0].transcript + " "
        user_message = user_message.strip()


        logger.info(f"Transcribed: {user_message}")
        logger.info(f"  STT: {stt_time:.3f}s")


        if not user_message:
            logger.warning("Empty transcription")
            return jsonify({'error': 'Could not transcribe audio'}), 400


        # Step 2 & 3: Gemini + TTS streaming with conversation context
        def generate():
            logger.info("Step 2: Streaming from Gemini...")
            first_chunk_time = None
            gemini_start = time.time()

            # Build prompt with conversation history
            prompt_with_context = build_context_prompt(session_data, user_message)

            input_tokens = len(prompt_with_context.split()) * 1.3
            logger.info(f" Gemini Input: ~{int(input_tokens)} tokens")
            if session_data['history']:
                logger.info(f" Using {len(session_data['history'])} previous exchanges for context")


            response = gemini_model.generate_content(prompt_with_context, stream=True)


            total_chars = 0
            output_tokens = 0
            sentence_buffer = ""
            sentence_count = 0
            full_ai_response = ""  # Capture complete response for history


            male_voices = ['en-US-Neural2-A', 'en-US-Neural2-D', 'en-US-Neural2-I', 'en-US-Neural2-J']
            gender = texttospeech.SsmlVoiceGender.MALE if selected_voice in male_voices else texttospeech.SsmlVoiceGender.FEMALE


            for chunk in response:
                # Check if stream was cancelled
                if active_streams.get(stream_id, False):
                    logger.info(f" Stream {stream_id} CANCELLED - stopping generation")
                    return

                if hasattr(chunk, 'text') and chunk.text and chunk.text.strip():
                    if first_chunk_time is None:
                        first_chunk_time = time.time() - gemini_start
                        logger.info(f"⏱  Time to First Token: {first_chunk_time:.3f}s")


                    total_chars += len(chunk.text)
                    output_tokens += len(chunk.text.split()) * 1.3
                    sentence_buffer += chunk.text
                    full_ai_response += chunk.text  # Build complete response


                    sentences = []
                    for delimiter in ['. ', '! ', '? ', '\n']:
                        if delimiter in sentence_buffer:
                            parts = sentence_buffer.split(delimiter)
                            for i in range(len(parts) - 1):
                                sentences.append(parts[i] + delimiter)
                            sentence_buffer = parts[-1]
                            break


                    for sentence in sentences:
                        if sentence.strip():
                            sentence_count += 1
                            sentence_start = time.time()
                            logger.info(f"Sentence {sentence_count}: {sentence}")

                            tts_start = time.time()
                            audio_base64, word_timing_data = synthesize_sentence_with_timing(sentence, selected_voice)
                            tts_time = time.time() - tts_start

                            sentence_time = time.time() - sentence_start
                            logger.info(f"⏱  Sentence {sentence_count} - TTS: {tts_time:.3f}s, Total: {sentence_time:.3f}s")

                            data = {
                                'text': sentence,
                                'audio': audio_base64,
                                'words': word_timing_data
                            }
                            yield f"data: {json.dumps(data)}\n\n"


            # Final buffer
            if sentence_buffer.strip():
                sentence_count += 1
                sentence_start = time.time()
                logger.info(f"Final buffer: {sentence_buffer}")

                tts_start = time.time()
                audio_base64, word_timing_data = synthesize_sentence_with_timing(sentence_buffer, selected_voice)
                tts_time = time.time() - tts_start

                sentence_time = time.time() - sentence_start
                logger.info(f"⏱  Final - TTS: {tts_time:.3f}s, Total: {sentence_time:.3f}s")

                data = {
                    'text': sentence_buffer,
                    'audio': audio_base64,
                    'words': word_timing_data
                }
                yield f"data: {json.dumps(data)}\n\n"


            total_time = time.time() - pipeline_start
            logger.info(f"⏱  TOTAL PIPELINE: {total_time:.3f}s (STT: {stt_time:.3f}s)")

            # Save conversation to session history
            add_to_conversation_history(session_id, user_message, full_ai_response.strip())
            logger.info(f" Saved exchange to session {session_id[:8]} (total: {len(session_data['history'])})")

            yield "data: {\"done\": true}\n\n"


        response = Response(stream_with_context(generate()), mimetype='text/event-stream')
        response.headers['X-Stream-ID'] = stream_id
        response.set_cookie('session_id', session_id, max_age=SESSION_TIMEOUT, httponly=True, samesite='Lax')
        return response


    except Exception as e:
        logger.error(f"Error in voice_unified: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500
    finally:
        # Cleanup stream tracking
        active_streams.pop(stream_id, None)
        logger.info(f" Cleaned up stream {stream_id}")


if __name__ == '__main__':
    app.run(debug=True, port=5000)