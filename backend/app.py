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

# Load environment variables
load_dotenv()

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
        logger.info(f"Decoded audio bytes, length: {len(audio_bytes)}")

        # Save audio file
        with open(WAVE_OUTPUT_FILENAME, 'wb') as f:
            f.write(audio_bytes)
        logger.info(f"Saved audio to {WAVE_OUTPUT_FILENAME}")

        # Transcribe with faster-whisper (4x faster)
        logger.info("Starting transcription with faster-whisper...")
        segments, info = whisper_model.transcribe(
            WAVE_OUTPUT_FILENAME,
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

        # Text to speech with Piper (much better quality than pyttsx3)
        output_file = "tts_output.wav"

        # Run Piper TTS with downloaded model
        result = subprocess.run(
            ['piper', '--model', 'en_US-lessac-medium.onnx', '--output_file', output_file],
            input=text.encode('utf-8'),
            capture_output=True,
            check=True
        )

        # Play the audio file
        if os.name == 'nt':  # Windows
            os.system(f'start /min "" "{output_file}"')
        else:  # Linux/Mac
            subprocess.run(['aplay', output_file])

        logger.info("Speech completed")

        return jsonify({'status': 'success'})

    except Exception as e:
        logger.error(f"Error in speak: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
