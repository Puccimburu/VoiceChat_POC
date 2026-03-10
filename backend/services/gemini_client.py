"""Shared Gemini API helpers — retry logic and streaming."""
import random
import time
import logging

from google.genai import types

from constants import _MAX_RETRIES

logger = logging.getLogger("ws_gateway")

_RETRYABLE_ERRORS = (
    "503", "UNAVAILABLE", "SSLV3", "BAD_RECORD_MAC",
    "SSL", "Server disconnected", "RemoteProtocol", "Connection reset",
)


def gemini_call(client, contents, config, model: str = "gemini-2.5-flash-lite"):
    """Call Gemini with exponential backoff on transient errors."""
    for attempt in range(_MAX_RETRIES):
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as e:
            msg = str(e)
            is_retryable = (
                any(tag in msg for tag in _RETRYABLE_ERRORS)
                or "overload" in msg.lower()
            )
            if is_retryable and attempt < _MAX_RETRIES - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                print(f"[Gemini] transient error ({msg[:60]}), retry {attempt + 1}/{_MAX_RETRIES - 1} in {wait:.1f}s")
                time.sleep(wait)
            else:
                raise


def gemini_stream_content(client, contents, config, on_text_chunk, model: str = "gemini-2.5-flash-lite"):
    """Stream Gemini, calling on_text_chunk(str) for each text piece as it arrives.
    Returns assembled model_content (text + any function_call parts).
    Falls back to gemini_call if the stream fails before any text is emitted."""
    all_text = ""
    fc_parts  = []
    emitted   = False
    try:
        for chunk in client.models.generate_content_stream(
            model=model, contents=contents, config=config
        ):
            if not chunk.candidates:
                continue
            for p in (chunk.candidates[0].content.parts or []):
                if hasattr(p, "function_call") and p.function_call:
                    fc_parts.append(p)
                elif hasattr(p, "text") and p.text:
                    all_text += p.text
                    on_text_chunk(p.text)
                    emitted = True
        merged = ([types.Part(text=all_text)] if all_text else []) + fc_parts
        return types.Content(role="model", parts=merged)
    except Exception as e:
        if not emitted:
            logger.warning(f"[Agent] stream failed before emission, falling back: {e}")
            resp = gemini_call(client, contents, config, model=model)
            return resp.candidates[0].content
        logger.warning(f"[Agent] stream interrupted mid-response: {e}")
        return types.Content(role="model",
                             parts=([types.Part(text=all_text)] if all_text else []) + fc_parts)
