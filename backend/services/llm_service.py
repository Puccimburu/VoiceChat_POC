"""LLM (Gemini) service"""
import logging
import random
import time
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)
_config = types.GenerateContentConfig(systemInstruction=GEMINI_SYSTEM_INSTRUCTION)
_MAX_RETRIES = 4


def prewarm_gemini():
    """Pre-warm Gemini connection to reduce TTFB"""
    try:
        client.models.generate_content(model=GEMINI_MODEL, contents="hi", config=_config)
    except Exception:
        pass  # Ignore errors, this is just for warming


def generate_response_stream(prompt):
    """Generate streaming response from Gemini with retry on 503 overload."""
    for attempt in range(_MAX_RETRIES):
        try:
            yield from client.models.generate_content_stream(
                model=GEMINI_MODEL, contents=prompt, config=_config
            )
            return
        except Exception as e:
            msg = str(e)
            is_overload = "503" in msg or "UNAVAILABLE" in msg or "overload" in msg.lower()
            if is_overload and attempt < _MAX_RETRIES - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"[Gemini] 503 overload, retry {attempt + 1}/{_MAX_RETRIES - 1} in {wait:.1f}s")
                time.sleep(wait)
            else:
                raise
