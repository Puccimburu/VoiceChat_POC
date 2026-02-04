"""LLM (Gemini) service"""
import logging
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)
_config = types.GenerateContentConfig(systemInstruction=GEMINI_SYSTEM_INSTRUCTION)


def prewarm_gemini():
    """Pre-warm Gemini connection to reduce TTFB"""
    try:
        client.models.generate_content(model=GEMINI_MODEL, contents="hi", config=_config)
    except Exception:
        pass  # Ignore errors, this is just for warming


def generate_response_stream(prompt):
    """Generate streaming response from Gemini"""
    return client.models.generate_content_stream(model=GEMINI_MODEL, contents=prompt, config=_config)
