"""LLM (Gemini) service"""
import logging
import google.generativeai as genai
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    GEMINI_MODEL,
    system_instruction=GEMINI_SYSTEM_INSTRUCTION
)


def prewarm_gemini():
    """Pre-warm Gemini connection to reduce TTFB"""
    try:
        gemini_model.generate_content("hi", stream=False)
    except Exception:
        pass  # Ignore errors, this is just for warming


def generate_response_stream(prompt):
    """Generate streaming response from Gemini

    Args:
        prompt: Input prompt with context

    Returns:
        Generator yielding response chunks
    """
    return gemini_model.generate_content(prompt, stream=True)
