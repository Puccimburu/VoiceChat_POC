"""Flask + SocketIO voice streaming server"""
import os
import sys
import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from flask import Flask, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from google.cloud import speech

load_dotenv()

from services.session_service import get_or_create_session, add_to_conversation_history, build_context_prompt
from services.stt_service import transcribe_audio
from services.llm_service import generate_response_stream, prewarm_gemini
from services.tts_service import synthesize_sentence_with_timing

# Logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s', force=True)
logger = logging.getLogger(__name__)
sys.stdout.reconfigure(line_buffering=True)

# App setup
app = Flask(__name__, static_folder='build', static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')


# --- Helpers ---

def detect_stt_encoding(mime_type, audio_header):
    """Determine STT encoding from MIME type, falling back to magic bytes"""
    if 'ogg' in mime_type.lower():
        return speech.RecognitionConfig.AudioEncoding.OGG_OPUS
    if 'webm' in mime_type.lower():
        return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
    if audio_header.startswith('1a45dfa3'):
        return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
    if audio_header.startswith('4f676753'):
        return speech.RecognitionConfig.AudioEncoding.OGG_OPUS
    logger.warning(f"⚠️ Unknown format (header: {audio_header[:8]}), defaulting to OGG_OPUS")
    return speech.RecognitionConfig.AudioEncoding.OGG_OPUS


def extract_sentences(buffer):
    """Split buffer at the first sentence boundary.
    Returns (list of complete sentences, remaining buffer)."""
    for delimiter in ['. ', '! ', '? ', '\n']:
        if delimiter in buffer:
            parts = buffer.split(delimiter)
            sentences = [parts[i] + delimiter for i in range(len(parts) - 1)]
            return sentences, parts[-1]
    return [], buffer


# --- Routes ---

@app.route('/')
def serve():
    return send_from_directory(app.static_folder, 'index.html')


# --- Socket events ---

@socketio.on('connect')
def handle_connect():
    logger.info(f"🔌 Connected: {request.sid}")
    emit('connected', {'status': 'ready'})


@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"🔌 Disconnected: {request.sid}")


@socketio.on('audio_complete')
def handle_audio_complete(data):
    """Full pipeline: audio blob → STT → Gemini → parallel TTS → stream chunks back"""
    client_sid = request.sid
    logger.info(f"📥 Received audio from client: {client_sid}")

    session_id = data.get('session_id')
    selected_voice = data.get('voice', 'en-US-Neural2-J')
    audio_data = data.get('audio')
    mime_type = data.get('mimeType', 'audio/webm;codecs=opus')

    if not audio_data:
        emit('error', {'message': 'No audio data received'})
        return

    session_id, session_data = get_or_create_session(session_id)

    try:
        # --- Decode & validate ---
        audio_bytes = base64.b64decode(audio_data.split(',')[1])
        audio_size_kb = len(audio_bytes) / 1000
        logger.info(f"📊 Audio: {audio_size_kb:.0f}KB | MIME: {mime_type}")

        if audio_size_kb < 30:
            logger.warning(f"⏭️ Skipping — too small ({audio_size_kb:.0f}KB), likely noise")
            emit('stream_complete', {'status': 'done', 'message': 'No speech detected', 'session_id': session_id})
            return

        # --- STT ---
        audio_header = audio_bytes[:20].hex()
        stt_encoding = detect_stt_encoding(mime_type, audio_header)
        logger.info(f"🔍 Header: {audio_header} → {stt_encoding}")

        pipeline_start = time.time()
        stt_start = time.time()
        transcript = transcribe_audio(audio_bytes, encoding=stt_encoding)
        stt_time = time.time() - stt_start
        logger.info(f"📝 Transcript: \"{transcript}\" ({stt_time:.3f}s)")

        if not transcript or not transcript.strip():
            emit('stream_complete', {'status': 'done', 'message': 'No speech detected', 'session_id': session_id})
            return

        # --- Gemini + parallel TTS ---
        prewarm_gemini()
        prompt = build_context_prompt(session_data, transcript)
        logger.info(f"💬 Gemini input: ~{len(prompt.split())} words")

        response_stream = generate_response_stream(prompt)
        llm_start = time.time()
        first_chunk_time = None
        sentence_buffer = ""
        full_ai_response = ""
        sentence_count = 0
        next_to_emit = 1
        completed_results = {}

        def tts_worker(sentence, num):
            t0 = time.time()
            audio_b64, timing = synthesize_sentence_with_timing(sentence, selected_voice)
            logger.info(f"⏱️ TTS #{num}: {time.time() - t0:.3f}s")
            return {'num': num, 'text': sentence, 'audio': audio_b64, 'words': timing}

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {}

            for chunk in response_stream:
                if first_chunk_time is None:
                    first_chunk_time = time.time()
                    logger.info(f"⏱️ TTFB: {first_chunk_time - llm_start:.3f}s")

                try:
                    chunk_text = chunk.text if hasattr(chunk, 'text') else None
                except (ValueError, AttributeError):
                    continue

                if chunk_text and chunk_text.strip():
                    sentence_buffer += chunk_text
                    full_ai_response += chunk_text

                    sentences, sentence_buffer = extract_sentences(sentence_buffer)
                    for s in sentences:
                        if s.strip():
                            sentence_count += 1
                            logger.info(f"📝 Sentence {sentence_count}: {s}")
                            futures[executor.submit(tts_worker, s, sentence_count)] = sentence_count

                # Flush completed TTS in order
                done = [f for f in futures if f.done()]
                for f in done:
                    result = f.result()
                    completed_results[result['num']] = result
                    del futures[f]
                while next_to_emit in completed_results:
                    result = completed_results.pop(next_to_emit)
                    logger.info(f"📤 Chunk #{next_to_emit}")
                    socketio.emit('audio_chunk', {
                        'text': result['text'], 'audio': result['audio'], 'words': result['words']
                    }, room=client_sid)
                    next_to_emit += 1

            # Final sentence fragment
            if sentence_buffer.strip():
                sentence_count += 1
                futures[executor.submit(tts_worker, sentence_buffer, sentence_count)] = sentence_count

            # Drain remaining futures in order
            for f in as_completed(futures):
                result = f.result()
                completed_results[result['num']] = result
            while next_to_emit in completed_results:
                result = completed_results.pop(next_to_emit)
                logger.info(f"📤 Final chunk #{next_to_emit}")
                socketio.emit('audio_chunk', {
                    'text': result['text'], 'audio': result['audio'], 'words': result['words']
                }, room=client_sid)
                next_to_emit += 1

        # --- Done ---
        pipeline_duration = time.time() - pipeline_start
        logger.info(f"✅ Response: \"{full_ai_response.strip()}\"")
        logger.info(f"⏱️ Pipeline: {pipeline_duration:.3f}s (STT: {stt_time:.3f}s)")

        add_to_conversation_history(session_id, transcript, full_ai_response.strip())
        logger.info(f"💾 Saved to session {session_id[:8]}")
        emit('stream_complete', {'status': 'done', 'session_id': session_id})

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        emit('error', {'message': str(e)})


if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
