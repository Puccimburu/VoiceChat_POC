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
CHUNK_SIZE = 32768  # 32KB chunks for streaming

# STT Configuration
STT_ENCODING = "LINEAR16"  # Raw PCM for streaming
STT_SAMPLE_RATE = 16000  # 16kHz for Google STT
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

# Pricing Configuration (USD per unit)
# Google Cloud Speech-to-Text streaming pricing
STT_PRICE_PER_15_SECONDS = 0.0024  # $0.0024 per 15 seconds

# Google Gemini 2.5 Flash Lite pricing
GEMINI_INPUT_PRICE_PER_1M_TOKENS = 0.0  # Free tier
GEMINI_OUTPUT_PRICE_PER_1M_TOKENS = 0.0  # Free tier

# Google Cloud Text-to-Speech pricing
TTS_PRICE_PER_1M_CHARS = 16.00  # Standard voices: $16 per 1M characters
