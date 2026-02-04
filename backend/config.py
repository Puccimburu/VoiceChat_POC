"""Application configuration"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Keys
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Session Configuration
SESSION_TIMEOUT = 24 * 60 * 60  # 24 hours in seconds
MAX_HISTORY = 5  # Keep last 5 conversation exchanges

# Audio Configuration
MAX_AUDIO_BYTES = 10000000  # 10MB max

# STT Configuration
STT_LANGUAGE = "en-US"
STT_MODEL = "latest_long"

# Gemini Configuration
GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_SYSTEM_INSTRUCTION = (
    "You are a helpful voice assistant. Always respond in English. "
    "Answer directly — no preamble, no hedging phrases like 'I think', 'It seems', or 'You might be asking'. "
    "Keep responses to 1-2 sentences unless the user explicitly asks for details or a long explanation. "
    "Do not use markdown formatting like asterisks, bold, or italics. "
    "Speak naturally as if in a conversation."
)

# TTS Configuration
TTS_SAMPLE_RATE = 24000
TTS_SPEAKING_RATE = 1.1
MALE_VOICES = ['en-US-Neural2-A', 'en-US-Neural2-D', 'en-US-Neural2-I', 'en-US-Neural2-J']
