"""Session management service — Redis-backed for persistence across restarts"""
import os
import uuid
import json
import logging
from datetime import datetime

import redis as redis_lib

from config import (
    SESSION_TIMEOUT,
    MAX_HISTORY,
    CONVERSATIONAL_PROMPT,
    DETAIL_PREFERENCE_RESPONSE_PROMPT,
    DOCUMENT_QUERY_PROMPT,
)

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------
_redis_client = None

def _get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _key(session_id: str) -> str:
    return f"session:{session_id}"


def _load(session_id: str) -> dict | None:
    try:
        raw = _get_redis().get(_key(session_id))
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"Redis load failed for {session_id}: {e}")
        return None


def _save(session_id: str, data: dict):
    try:
        _get_redis().setex(_key(session_id), SESSION_TIMEOUT, json.dumps(data))
    except Exception as e:
        logger.warning(f"Redis save failed for {session_id}: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_or_create_session(session_id=None):
    """Get existing session from Redis or create a new one.

    Returns:
        (session_id, session_data) — session_data is a plain dict:
            {
              'history':     list of {user, assistant, timestamp},
              'variables':   dict of custom context,
              'created':     ISO timestamp string,
              'last_access': ISO timestamp string,
            }
    """
    now = datetime.now().isoformat()

    if not session_id:
        session_id = str(uuid.uuid4())

    data = _load(session_id)

    if data is None:
        data = {
            'history': [],
            'variables': {},
            'created': now,
            'last_access': now,
        }
        _save(session_id, data)
        logger.info(f"Created new session {session_id}")
    else:
        data['last_access'] = now
        _save(session_id, data)  # refresh TTL

    return session_id, data


def add_to_conversation_history(session_id: str, user_message: str, ai_response: str):
    """Append a conversation exchange to the session history in Redis."""
    data = _load(session_id)
    if data is None:
        logger.warning(f"add_to_conversation_history: session {session_id} not found")
        return

    data['history'].append({
        'user': user_message,
        'assistant': ai_response,
        'timestamp': datetime.now().isoformat(),
    })
    # Trim to MAX_HISTORY
    data['history'] = data['history'][-MAX_HISTORY:]
    _save(session_id, data)


def save_session(session_id: str, session_data: dict):
    """Explicitly persist session_data back to Redis.

    Call this after any code that mutates session_data['variables'] in-place
    (e.g. build_rag_prompt setting detail_preference).
    """
    _save(session_id, session_data)


# ---------------------------------------------------------------------------
# Prompt builders (stateless — take session_data dict, return string)
# ---------------------------------------------------------------------------

def build_context_prompt(session_data: dict, current_message: str) -> str:
    """Build Gemini prompt with conversation history."""
    if not session_data['history']:
        return current_message

    history_text = "Previous conversation:\n"
    for exchange in session_data['history'][-5:]:
        history_text += f"User: {exchange['user']}\n"
        history_text += f"Assistant: {exchange['assistant']}\n\n"

    if session_data['variables']:
        history_text += f"Context: {session_data['variables']}\n\n"

    history_text += f"Current question: {current_message}"
    return history_text


def build_rag_prompt(session_data: dict, current_message: str, qdrant_results: list) -> str:
    """Build Gemini prompt with Qdrant search results as context."""
    sorted_results = sorted(qdrant_results, key=lambda p: p.payload.get('chunk_index', 0))

    excerpts = [p.payload.get('text', '') for p in sorted_results if p.payload.get('text', '').strip()]
    excerpts_text = "\n\n".join(excerpts)

    history_text = ""
    if session_data['history']:
        history_text = "Previous conversation:\n"
        for exchange in session_data['history'][-5:]:
            history_text += f"User: {exchange['user']}\n"
            history_text += f"Assistant: {exchange['assistant']}\n\n"

    # Detect if last exchange asked about detail preference
    last_asked_preference = False
    original_question = current_message

    if session_data['history']:
        last_response = session_data['history'][-1].get('assistant', '').lower()
        last_asked_preference = 'summary or' in last_response and 'detailed' in last_response
        if last_asked_preference:
            original_question = session_data['history'][-1].get('user', current_message)

    word_count = len(current_message.split())
    starts_with_greeting = current_message.lower().startswith(('hello', 'hi', 'hey', 'good', 'thanks', 'thank'))
    is_likely_conversation = word_count <= 3 or starts_with_greeting

    user_requesting_detailed = any(w in current_message.lower() for w in ['detailed', 'detail', 'full', 'complete', 'thorough', 'in detail'])
    user_requesting_summary = any(w in current_message.lower() for w in ['summary', 'summarize', 'brief', 'short', 'concise'])

    if last_asked_preference:
        if user_requesting_detailed:
            session_data['variables']['detail_preference'] = 'detailed'
        elif user_requesting_summary:
            session_data['variables']['detail_preference'] = 'summary'
        else:
            session_data['variables']['detail_preference'] = 'detailed'

        prompt = DETAIL_PREFERENCE_RESPONSE_PROMPT.format(
            original_question=original_question,
            current_message=current_message,
            excerpts_text=excerpts_text,
        )
    elif is_likely_conversation:
        prompt = CONVERSATIONAL_PROMPT
    else:
        if user_requesting_detailed:
            session_data['variables']['detail_preference'] = 'detailed'
        elif user_requesting_summary:
            session_data['variables']['detail_preference'] = 'summary'

        saved_preference = session_data['variables'].get('detail_preference', 'detailed')
        prompt = DOCUMENT_QUERY_PROMPT.format(
            current_message=current_message,
            saved_preference=saved_preference,
            excerpts_text=excerpts_text,
        )

    if history_text:
        prompt += history_text

    prompt += f"Current question: {current_message}"
    return prompt
