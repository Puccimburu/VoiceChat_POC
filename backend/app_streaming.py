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
from services.streaming_stt_service import StreamingSTT

# Per-client streaming STT sessions
streaming_sessions = {}

# Per-client active request tracking — pipeline checks its own request_id against this
active_requests = {}  # sid → request_id (or None after barge-in)

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

def extract_sentences(buffer):
    """Split buffer at the first sentence boundary.
    Returns (list of complete sentences, remaining buffer)."""
    for delimiter in ['. ', '! ', '? ', '\n']:
        if delimiter in buffer:
            parts = buffer.split(delimiter)
            sentences = [parts[i] + delimiter for i in range(len(parts) - 1)]
            return sentences, parts[-1]
    return [], buffer


def _emit_ordered(completed_results, next_to_emit, sid, request_id):
    """Emit consecutively-ready TTS chunks in order. Returns updated next_to_emit."""
    while next_to_emit in completed_results:
        if active_requests.get(sid) != request_id:
            return next_to_emit
        result = completed_results.pop(next_to_emit)
        logger.info(f" Chunk #{next_to_emit}")
        socketio.emit('audio_chunk', {
            'text': result['text'], 'audio': result['audio'], 'words': result['words'],
            'request_id': request_id
        }, room=sid)
        next_to_emit += 1
    return next_to_emit


# --- Routes ---

@app.route('/')
def serve():
    return send_from_directory(app.static_folder, 'index.html')


# --- Socket events ---

@socketio.on('connect')
def handle_connect():
    logger.info(f" Connected: {request.sid}")
    emit('connected', {'status': 'ready'})


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    logger.info(f"Disconnected: {sid}")
    # Clean up any active streaming session
    if sid in streaming_sessions:
        streaming_sessions[sid]['stt'].close()
        del streaming_sessions[sid]
    active_requests.pop(sid, None)


@socketio.on('barge_in')
def handle_barge_in(data):
    """Handle user barge-in - cancel ongoing pipeline"""
    sid = request.sid
    logger.info(f" Barge-in received from {sid} - cancelling pipeline")
    active_requests[sid] = None  # Any running pipeline will see its request_id no longer matches
    # Also close any active streaming STT session
    if sid in streaming_sessions:
        try:
            streaming_sessions[sid]['stt'].close()
        except Exception as e:
            logger.error(f" Error closing STT on barge-in: {e}")
        del streaming_sessions[sid]


# --- Streaming STT handlers ---

@socketio.on('start_stream')
def handle_start_stream(data):
    """Initialize streaming STT session for this client"""
    sid = request.sid
    session_id = data.get('session_id')
    selected_voice = data.get('voice', 'en-US-Neural2-J')
    mime_type = data.get('mimeType', 'audio/webm;codecs=opus')

    # Prevent multiple concurrent sessions for same client
    if sid in streaming_sessions:
        logger.warning(f" Client {sid} already has active streaming session, closing old one")
        try:
            streaming_sessions[sid]['stt'].close()
        except Exception as e:
            logger.error(f" Error closing old session: {e}")
        del streaming_sessions[sid]

    logger.info(f"Starting streaming STT for {sid} (mime: {mime_type})")

    if 'pcm' in mime_type.lower():
        encoding = speech.RecognitionConfig.AudioEncoding.LINEAR16
    elif 'ogg' in mime_type.lower():
        encoding = speech.RecognitionConfig.AudioEncoding.OGG_OPUS
    else:
        encoding = speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
    stt = StreamingSTT(encoding=encoding, sample_rate=48000)

    stt.start()

    # Store session data
    session_id, session_data = get_or_create_session(session_id)
    streaming_sessions[sid] = {
        'stt': stt,
        'session_id': session_id,
        'session_data': session_data,
        'voice': selected_voice,
        'start_time': time.time()
    }

    emit('stream_started', {'session_id': session_id})


@socketio.on('stt_audio')
def handle_stt_audio(data):
    """Receive audio chunk for streaming STT"""
    sid = request.sid
    if sid not in streaming_sessions:
        logger.debug(f" Received audio for unknown session {sid}")
        return

    audio_b64 = data.get('audio', '')
    if ',' in audio_b64:
        audio_b64 = audio_b64.split(',')[1]

    try:
        audio_bytes = base64.b64decode(audio_b64)
        if len(audio_bytes) > 0:
            streaming_sessions[sid]['stt'].add_audio_chunk(audio_bytes)
            logger.debug(f" Added {len(audio_bytes)} bytes to STT queue")
    except Exception as e:
        logger.error(f" Error decoding audio chunk: {e}")


@socketio.on('end_speech')
def handle_end_speech(data):
    """End streaming STT and run LLM+TTS pipeline"""
    sid = request.sid

    if sid not in streaming_sessions:
        logger.warning(f" No streaming session for {sid}")
        emit('error', {'message': 'No active streaming session'})
        return

    session = streaming_sessions.pop(sid)
    stt = session['stt']
    session_id = session['session_id']
    session_data = session['session_data']
    selected_voice = session['voice']
    pipeline_start = session['start_time']
    request_id = data.get('request_id')

    # Register as the active request for this client
    active_requests[sid] = request_id

    speech_duration = time.time() - pipeline_start
    logger.info(f" Ending streaming STT for {sid} (speech duration: {speech_duration:.2f}s)")

    # Latency the user perceives starts here (after speech ends)
    pipeline_start = time.time()
    stt.close()
    transcript = stt.wait_for_result(timeout=5.0)
    stt_time = time.time() - pipeline_start

    buffer_size = len(stt._audio_buffer)
    logger.info(f" Streaming transcript: \"{transcript}\" (finalize: {stt_time:.3f}s, buffer: {buffer_size} bytes)")

    # Fallback: if streaming returned nothing but we have buffered PCM, batch-transcribe it
    if (not transcript or not transcript.strip()) and buffer_size > 0:
        logger.info(f" Streaming empty — batch fallback on {buffer_size} bytes of PCM")
        transcript = transcribe_audio(
            bytes(stt._audio_buffer),
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16
        )
        logger.info(f" Batch fallback transcript: \"{transcript}\"")

    if not transcript or not transcript.strip():
        emit('stream_complete', {'status': 'done', 'message': 'No speech detected', 'session_id': session_id})
        return

    # --- LLM + TTS pipeline ---
    try:
        prewarm_gemini()
        prompt = build_context_prompt(session_data, transcript)
        logger.info(f" Gemini input: ~{len(prompt.split())} words")

        response_stream = generate_response_stream(prompt)
        llm_start = time.time()
        ttfb_logged = False
        sentence_buffer = ""
        full_ai_response = ""
        sentence_count = 0
        next_to_emit = 1
        completed_results = {}

        def tts_worker(sentence, num):
            t0 = time.time()
            audio_b64, timing = synthesize_sentence_with_timing(sentence, selected_voice)
            logger.info(f"TTS #{num}: {time.time() - t0:.3f}s")
            return {'num': num, 'text': sentence, 'audio': audio_b64, 'words': timing}

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {}

            for chunk in response_stream:
                if active_requests.get(sid) != request_id:
                    logger.info(f" Pipeline cancelled — no longer the active request")
                    break

                if not ttfb_logged:
                    logger.info(f"TTFB: {time.time() - llm_start:.3f}s")
                    ttfb_logged = True

                try:
                    chunk_text = chunk.text
                except (ValueError, AttributeError):
                    continue

                if chunk_text and chunk_text.strip():
                    sentence_buffer += chunk_text
                    full_ai_response += chunk_text

                    sentences, sentence_buffer = extract_sentences(sentence_buffer)
                    for s in sentences:
                        if s.strip():
                            sentence_count += 1
                            logger.info(f" Sentence {sentence_count}: {s}")
                            futures[executor.submit(tts_worker, s, sentence_count)] = sentence_count

                # Collect finished futures, then emit any that are in order
                for f in [f for f in futures if f.done()]:
                    completed_results[f.result()['num']] = f.result()
                    del futures[f]
                next_to_emit = _emit_ordered(completed_results, next_to_emit, sid, request_id)

            # Final sentence fragment (skip if cancelled)
            if sentence_buffer.strip() and active_requests.get(sid) == request_id:
                sentence_count += 1
                futures[executor.submit(tts_worker, sentence_buffer, sentence_count)] = sentence_count

            # Drain remaining futures, then flush
            for f in as_completed(futures):
                completed_results[f.result()['num']] = f.result()
            next_to_emit = _emit_ordered(completed_results, next_to_emit, sid, request_id)

        # --- Done ---
        pipeline_duration = time.time() - pipeline_start
        logger.info(f" Response: \"{full_ai_response.strip()}\"")
        logger.info(f"Pipeline: {pipeline_duration:.3f}s (STT: {stt_time:.3f}s)")

        add_to_conversation_history(session_id, transcript, full_ai_response.strip())
        logger.info(f" Saved to session {session_id[:8]}")
        emit('stream_complete', {'status': 'done', 'session_id': session_id})

    except Exception as e:
        logger.error(f" Error in streaming pipeline: {e}", exc_info=True)
        emit('error', {'message': str(e)})


if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
