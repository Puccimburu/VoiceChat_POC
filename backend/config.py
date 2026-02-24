"""Application configuration"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Keys
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Platform database (stores customers, API keys, db configs)
PLATFORM_MONGO_URI = os.environ.get("PLATFORM_MONGO_URI", "mongodb://localhost:27017/")
PLATFORM_DB        = os.environ.get("PLATFORM_DB", "Test")

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

# Qdrant Configuration
QDRANT_CLUSTER_URL = os.environ.get("QDRANT_CLUSTER_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
QDRANT_COLLECTION_NAME = "voice_test"

# RAG Prompt Templates
CONVERSATIONAL_PROMPT = (
    "You are a helpful document assistant. Respond naturally to the user's message.\n"
    "Do not use markdown formatting. Speak naturally as if in a conversation.\n\n"
)

DETAIL_PREFERENCE_RESPONSE_PROMPT = (
    "You are a document assistant. The user originally asked: '{original_question}'\n"
    "They chose: {current_message}\n\n"
    "Now answer their original question:\n"
    "- If they chose SUMMARY/BRIEF: provide a concise 2-3 sentence answer.\n"
    "- If they chose DETAILED/FULL: provide a comprehensive, exhaustive answer with ALL relevant information "
    "from the document. Include every definition, clause, date, party name, amount, condition, and specific detail. "
    "Be thorough as if reading the entire relevant section to them.\n\n"
    "Do NOT include labels like 'SUMMARY:' or 'DETAILED:' in your response. Just provide the answer directly.\n"
    "Do not use markdown formatting. Speak naturally.\n\n"
    "--- Complete Document ---\n{excerpts_text}\n--- End Document ---\n\n"
)

DOCUMENT_QUERY_PROMPT = (
    "You are a document assistant. The user asked: '{current_message}'\n\n"
    "Answer their question using a {saved_preference} approach:\n"
    "- If SUMMARY: provide a concise 2-3 sentence answer.\n"
    "- If DETAILED: provide a comprehensive, exhaustive answer with ALL relevant information "
    "from the document. Include every definition, clause, date, party name, amount, condition, and specific detail.\n\n"
    "Do NOT include labels like 'SUMMARY:' or 'DETAILED:' in your response. Just provide the answer directly.\n"
    "Do not use markdown formatting. Speak naturally.\n\n"
    "--- Complete Document ---\n{excerpts_text}\n--- End Document ---\n\n"
)
