"""Flask application - Routes only"""
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import base64
import logging
import json
import time

# Import services
from services.session_service import (
    get_or_create_session,
    add_to_conversation_history,
    build_context_prompt,
    get_session,
    delete_session
)
from services.stt_service import transcribe_audio
from services.llm_service import generate_response_stream, prewarm_gemini
from services.tts_service import synthesize_sentence_with_timing
from utils.stream_manager import (
    cancel_active_streams,
    register_stream,
    is_stream_cancelled,
    cleanup_stream
)
from config import MAX_AUDIO_BYTES, SESSION_TIMEOUT

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='build', static_url_path='')
CORS(app)

logger.info("Google Cloud STT and TTS clients initialized!")


@app.route('/')
def serve():
    """Serve frontend"""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/cancel/<stream_id>', methods=['POST'])
def cancel_stream(stream_id):
    """Cancel an active streaming response"""
    from utils.stream_manager import active_streams
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
        session_data = get_session(session_id)
        if session_data:
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
        if delete_session(session_id):
            return jsonify({'status': 'session_reset'})
        return jsonify({'error': 'No active session'}), 404


@app.route('/api/voice', methods=['POST'])
def voice_unified():
    """Unified endpoint: Audio → STT → Gemini → TTS (streaming)"""
    stream_id = str(time.time())

    # Get or create session
    session_id_from_request = request.cookies.get('session_id') or request.headers.get('X-Session-ID')
    session_id, session_data = get_or_create_session(session_id_from_request)

    # Auto-interrupt any active streams
    cancel_active_streams()
    register_stream(stream_id)

    try:
        # Get audio and voice preference
        audio_data = request.json['audio']
        selected_voice = request.json.get('voice', 'en-US-Neural2-J')
        logger.info(f" Selected voice: {selected_voice}")

        logger.info(f"=== Starting unified voice pipeline (stream {stream_id}, session {session_id[:8]}) ===")
        pipeline_start = time.time()

        # Step 1: Speech-to-Text
        logger.info("Step 1: Transcribing audio...")
        audio_bytes = base64.b64decode(audio_data.split(',')[1])
        audio_size_bytes = len(audio_bytes)
        logger.info(f" Audio: {audio_size_bytes/1000:.0f}KB")

        if audio_size_bytes > MAX_AUDIO_BYTES:
            logger.warning(f" REJECTED: {audio_size_bytes/1000:.0f}KB > {MAX_AUDIO_BYTES/1000}KB")
            return jsonify({'error': 'Audio too long. Maximum 10MB allowed.'}), 400

        stt_start = time.time()
        user_message = transcribe_audio(audio_bytes)
        stt_time = time.time() - stt_start

        logger.info(f"Transcribed: {user_message}")
        logger.info(f"  STT: {stt_time:.3f}s")

        # If empty transcription, use fallback
        if not user_message:
            logger.warning("Empty transcription - using fallback prompt")
            user_message = "I didn't catch that. Could you please repeat?"

        # Pre-warm Gemini
        logger.info(" Pre-warming Gemini...")
        prewarm_gemini()

        # Step 2 & 3: Gemini + TTS streaming
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

            response = generate_response_stream(prompt_with_context)

            sentence_buffer = ""
            sentence_count = 0
            full_ai_response = ""

            for chunk in response:
                # Check if stream was cancelled
                if is_stream_cancelled(stream_id):
                    logger.info(f" Stream {stream_id} CANCELLED - stopping generation")
                    return

                # Safely check for text content
                try:
                    chunk_text = chunk.text if hasattr(chunk, 'text') else None
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Chunk has no text: {e}")
                    continue

                if chunk_text and chunk_text.strip():
                    if first_chunk_time is None:
                        first_chunk_time = time.time() - gemini_start
                        logger.info(f"  Time to First Token: {first_chunk_time:.3f}s")

                    sentence_buffer += chunk_text
                    full_ai_response += chunk_text

                    # Split into sentences
                    sentences = []
                    for delimiter in ['. ', '! ', '? ', '\n']:
                        if delimiter in sentence_buffer:
                            parts = sentence_buffer.split(delimiter)
                            for i in range(len(parts) - 1):
                                sentences.append(parts[i] + delimiter)
                            sentence_buffer = parts[-1]
                            break

                    for sentence in sentences:
                        # Check for cancellation before TTS
                        if is_stream_cancelled(stream_id):
                            logger.info(f" Stream {stream_id} CANCELLED - stopping before TTS")
                            return

                        if sentence.strip():
                            sentence_count += 1
                            sentence_start = time.time()
                            logger.info(f"Sentence {sentence_count}: {sentence}")

                            tts_start = time.time()
                            audio_base64, word_timing_data = synthesize_sentence_with_timing(sentence, selected_voice)
                            tts_time = time.time() - tts_start

                            # Check again after TTS
                            if is_stream_cancelled(stream_id):
                                logger.info(f" Stream {stream_id} CANCELLED - stopping after TTS")
                                return

                            sentence_time = time.time() - sentence_start
                            logger.info(f"  Sentence {sentence_count} - TTS: {tts_time:.3f}s, Total: {sentence_time:.3f}s")

                            data = {
                                'text': sentence,
                                'audio': audio_base64,
                                'words': word_timing_data
                            }
                            yield f"data: {json.dumps(data)}\n\n"

            # Final buffer
            if sentence_buffer.strip():
                # Check for cancellation
                if is_stream_cancelled(stream_id):
                    logger.info(f" Stream {stream_id} CANCELLED - stopping before final buffer")
                    return

                sentence_count += 1
                sentence_start = time.time()
                logger.info(f"Final buffer: {sentence_buffer}")

                tts_start = time.time()
                audio_base64, word_timing_data = synthesize_sentence_with_timing(sentence_buffer, selected_voice)
                tts_time = time.time() - tts_start

                # Check again after TTS
                if is_stream_cancelled(stream_id):
                    logger.info(f" Stream {stream_id} CANCELLED - stopping after final TTS")
                    return

                sentence_time = time.time() - sentence_start
                logger.info(f"  Final - TTS: {tts_time:.3f}s, Total: {sentence_time:.3f}s")

                data = {
                    'text': sentence_buffer,
                    'audio': audio_base64,
                    'words': word_timing_data
                }
                yield f"data: {json.dumps(data)}\n\n"

            total_time = time.time() - pipeline_start
            logger.info(f"  TOTAL PIPELINE: {total_time:.3f}s (STT: {stt_time:.3f}s)")

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
        cleanup_stream(stream_id)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
