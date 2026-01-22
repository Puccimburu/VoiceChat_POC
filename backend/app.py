from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import pyaudio
import wave
from faster_whisper import WhisperModel
import google.generativeai as genai
import os
import base64
import io
import traceback
import logging
import subprocess
import threading
import numpy as np
import imageio_ffmpeg

# Load environment variables
load_dotenv()

# Get ffmpeg path from imageio-ffmpeg
ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='build', static_url_path='')
CORS(app)

# Configuration
AUDIO_FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024
RECORD_SECONDS = 5
WAVE_OUTPUT_FILENAME = "voice_input.wav"

# Configure Gemini API
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel(
    'gemini-2.5-flash-lite',
    system_instruction="You are a helpful voice assistant. Always respond in English. Keep responses concise and conversational (2-3 sentences max unless asked for details). Do not use markdown formatting like asterisks, bold, or italics. Speak naturally as if in a conversation. If the transcription seems unclear or might be a homophone (like 'close' vs 'clause'), consider context or ask for clarification."
)

# Load faster-whisper model once (4x faster than openai-whisper)
logger.info("Loading faster-whisper model...")
whisper_model = WhisperModel("medium", device="cpu", compute_type="int8")
logger.info("faster-whisper model loaded!")

@app.route('/')
def serve():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/transcribe', methods=['POST'])
def transcribe():
    try:
        logger.info("=== Starting transcription ===")

        # Get audio data from request
        audio_data = request.json['audio']
        logger.info(f"Received audio data, length: {len(audio_data)}")

        audio_bytes = base64.b64decode(audio_data.split(',')[1])
        logger.info(f"Decoded audio bytes, length: {len(audio_bytes)} bytes ({len(audio_bytes)/1024:.2f} KB)")

        # Process audio in-memory (FAST - no disk I/O, no file saved)
        logger.info("Converting audio to NumPy array (in-memory)...")

        # Use ffmpeg directly to convert audio to raw PCM (bypasses pydub's ffprobe dependency)
        ffmpeg_cmd = [
            ffmpeg_path,
            '-i', 'pipe:0',  # Read from stdin
            '-f', 's16le',   # Output format: signed 16-bit little-endian PCM
            '-acodec', 'pcm_s16le',
            '-ar', '16000',  # Sample rate: 16kHz
            '-ac', '1',      # Channels: mono
            'pipe:1'         # Write to stdout
        ]

        # Run ffmpeg process
        process = subprocess.run(
            ffmpeg_cmd,
            input=audio_bytes,
            capture_output=True,
            check=True
        )

        # Convert raw PCM bytes to NumPy array
        audio_array = np.frombuffer(process.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        logger.info(f"Converted to NumPy: {len(audio_array)} samples, duration: {len(audio_array)/16000:.2f}s")

        # Transcribe with faster-whisper (4x faster) - directly from NumPy array
        logger.info("Starting transcription with faster-whisper (in-memory)...")
        segments, info = whisper_model.transcribe(
            audio_array,  # NumPy array instead of filename - NO DISK I/O!
            language="en",
            temperature=0.0,
            condition_on_previous_text=False
        )

        # Combine all segments into final text
        text = " ".join([segment.text for segment in segments])
        logger.info(f"Transcription result: {text}")

        return jsonify({'text': text})

    except Exception as e:
        logger.error(f"Error in transcribe: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json['message']
        logger.info(f"Received chat message: {user_message}")

        # Get response from Gemini
        logger.info("Sending to Gemini...")
        response = gemini_model.generate_content(user_message)
        logger.info(f"Gemini response: {response.text}")

        return jsonify({'response': response.text})

    except Exception as e:
        logger.error(f"Error in chat: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/speak', methods=['POST'])
def speak():
    try:
        text = request.json['text']
        logger.info(f"Speaking text: {text[:50]}...")

        # Generate TTS to a temporary file, then read it
        output_file = "tts_output.wav"

        # Run Piper TTS with downloaded model
        result = subprocess.run(
            ['piper', '--model', 'en_US-lessac-medium.onnx', '--output_file', output_file],
            input=text.encode('utf-8'),
            capture_output=True,
            check=True
        )

        # Read the WAV file and send to browser
        with open(output_file, 'rb') as f:
            audio_data = f.read()

        # Convert to base64 for browser playback
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        logger.info(f"TTS audio generated: {len(audio_data)} bytes")

        return jsonify({
            'status': 'success',
            'audio': f'data:audio/wav;base64,{audio_base64}'
        })

    except Exception as e:
        logger.error(f"Error in speak: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)