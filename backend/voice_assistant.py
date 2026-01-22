import pyaudio
import wave
from faster_whisper import WhisperModel
import google.generativeai as genai
import os
from dotenv import load_dotenv
import subprocess

# Load environment variables
load_dotenv()

# Configuration
AUDIO_FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024
MAX_RECORD_SECONDS = 10  # Maximum recording time
WAVE_OUTPUT_FILENAME = "voice_input.wav"

# Configure Gemini API
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel(
    'gemini-2.5-flash-lite',
    system_instruction="You are a helpful voice assistant. Always respond in English. Keep responses concise and conversational (2-3 sentences max unless asked for details). Do not use markdown formatting like asterisks, bold, or italics. Speak naturally as if in a conversation."
)

# Load faster-whisper model ONCE at startup (4x faster than openai-whisper)
print("Loading faster-whisper model...")
whisper_model = WhisperModel("medium", device="cpu", compute_type="int8")
print("Whisper model loaded!")

def record_audio():
    """Record audio for fixed duration"""
    audio = pyaudio.PyAudio()

    stream = audio.open(format=AUDIO_FORMAT,
                       channels=CHANNELS,
                       rate=RATE,
                       input=True,
                       frames_per_buffer=CHUNK)

    print("Listening... (speak now)")
    frames = []
    max_frames = int(RATE / CHUNK * MAX_RECORD_SECONDS)

    for _ in range(max_frames):
        data = stream.read(CHUNK)
        frames.append(data)

    print("Recording finished")

    stream.stop_stream()
    stream.close()
    audio.terminate()

    # Save to WAV file
    wf = wave.open(WAVE_OUTPUT_FILENAME, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(audio.get_sample_size(AUDIO_FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(frames))
    wf.close()

def transcribe_audio():
    """Transcribe audio using faster-whisper (4x faster)"""
    print("Transcribing...")
    segments, info = whisper_model.transcribe(
        WAVE_OUTPUT_FILENAME,
        language="en",
        temperature=0.0,
        condition_on_previous_text=False
    )

    # Combine all segments into final text
    text = " ".join([segment.text for segment in segments])
    print(f"You said: {text}")
    return text

def get_gemini_response(text):
    """Get response from Gemini"""
    print("Thinking...")
    response = gemini_model.generate_content(text)
    print(f"Assistant: {response.text}")
    return response.text

def speak_text(text):
    """Speak text using Piper (high-quality neural TTS)"""
    print("Speaking...")
    output_file = "tts_output.wav"

    # Run Piper TTS with downloaded model
    subprocess.run(
        ['piper', '--model', 'en_US-lessac-medium.onnx', '--output_file', output_file],
        input=text.encode('utf-8'),
        check=True
    )

    # Play the audio file
    if os.name == 'nt':  # Windows
        os.system(f'"{output_file}"')
    else:  # Linux/Mac
        subprocess.run(['aplay', output_file])

def main():
    record_audio()
    user_text = transcribe_audio()
    assistant_response = get_gemini_response(user_text)
    speak_text(assistant_response)

if __name__ == "__main__":
    main()
