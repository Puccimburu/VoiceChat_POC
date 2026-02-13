"""Session management service"""
import uuid
from collections import deque
from datetime import datetime, timedelta
import logging
from config import (
    SESSION_TIMEOUT,
    MAX_HISTORY,
    CONVERSATIONAL_PROMPT,
    DETAIL_PREFERENCE_RESPONSE_PROMPT,
    DOCUMENT_QUERY_PROMPT,
)

logger = logging.getLogger(__name__)

# In-memory session storage
sessions = {}


def get_or_create_session(session_id=None):
    """Get existing session or create new one with context storage

    """
    now = datetime.now()

    # Clean up expired sessions
    expired_sessions = [
        sid for sid, data in sessions.items()
        if now - data['last_access'] > timedelta(seconds=SESSION_TIMEOUT)
    ]
    for sid in expired_sessions:
        del sessions[sid]
        logger.info(f"Cleaned up expired session {sid}")

    # Generate ID only if none provided
    if not session_id:
        session_id = str(uuid.uuid4())

    # Create session entry if it doesn't exist yet
    if session_id not in sessions:
        sessions[session_id] = {
            'history': deque(maxlen=MAX_HISTORY),
            'variables': {},
            'created': now,
            'last_access': now
        }
        logger.info(f" Created new session {session_id}")
    else:
        sessions[session_id]['last_access'] = now

    return session_id, sessions[session_id]


def add_to_conversation_history(session_id, user_message, ai_response):
    """Add exchange to session history"""
    if session_id in sessions:
        sessions[session_id]['history'].append({
            'user': user_message,
            'assistant': ai_response,
            'timestamp': datetime.now().isoformat()
        })


def build_context_prompt(session_data, current_message):
    """Build Gemini prompt with conversation history
    """
    if not session_data['history']:
        return current_message

    # Build conversation history (limit to last 5 exchanges for performance)
    history_text = "Previous conversation:\n"
    recent_history = list(session_data['history'])[-5:]  # Only last 5 exchanges
    for exchange in recent_history:
        history_text += f"User: {exchange['user']}\n"
        history_text += f"Assistant: {exchange['assistant']}\n\n"

    # Add custom variables if any
    if session_data['variables']:
        history_text += f"Context: {session_data['variables']}\n\n"

    history_text += f"Current question: {current_message}"
    return history_text


def build_rag_prompt(session_data, current_message, qdrant_results):
    """Build Gemini prompt with Qdrant search results as context."""
    # Sort by chunk_index so the document reads in natural order
    sorted_results = sorted(qdrant_results, key=lambda p: p.payload.get('chunk_index', 0))

    excerpts = []
    for point in sorted_results:
        text = point.payload.get('text', '')
        if text.strip():
            excerpts.append(text)

    excerpts_text = "\n\n".join(excerpts)

    # Build conversation history
    history_text = ""
    if session_data['history']:
        history_text = "Previous conversation:\n"
        for exchange in list(session_data['history'])[-5:]:
            history_text += f"User: {exchange['user']}\n"
            history_text += f"Assistant: {exchange['assistant']}\n\n"

    # Check if previous exchange asked about detail preference
    last_asked_preference = False
    original_question = current_message

    if session_data['history'] and len(session_data['history']) >= 1:
        last_response = list(session_data['history'])[-1].get('assistant', '').lower()
        last_asked_preference = 'summary or' in last_response and 'detailed' in last_response

        if last_asked_preference:
            # Get the original question from the last exchange (before we asked for preference)
            original_question = list(session_data['history'])[-1].get('user', current_message)

    # Simple heuristic: if message is very short (1-3 words) or starts with common greetings, it's likely not a document query
    word_count = len(current_message.split())
    starts_with_greeting = current_message.lower().startswith(('hello', 'hi', 'hey', 'good', 'thanks', 'thank'))

    # Treat as conversational if it's a short message or greeting
    is_likely_conversation = word_count <= 3 or starts_with_greeting

    # Check if user is explicitly requesting detail level
    user_requesting_detailed = any(word in current_message.lower() for word in ['detailed', 'detail', 'full', 'complete', 'thorough', 'in detail'])
    user_requesting_summary = any(word in current_message.lower() for word in ['summary', 'summarize', 'brief', 'short', 'concise'])

    if last_asked_preference:
        # User is responding to our detail preference question - save their preference
        if user_requesting_detailed:
            session_data['variables']['detail_preference'] = 'detailed'
        elif user_requesting_summary:
            session_data['variables']['detail_preference'] = 'summary'
        else:
            # Default to detailed if unclear
            session_data['variables']['detail_preference'] = 'detailed'

        prompt = DETAIL_PREFERENCE_RESPONSE_PROMPT.format(
            original_question=original_question,
            current_message=current_message,
            excerpts_text=excerpts_text
        )
    elif is_likely_conversation:
        # Short message or greeting - respond naturally
        prompt = CONVERSATIONAL_PROMPT
    else:
        # Longer message, likely a document question
        # Check if user is explicitly changing their preference in this message
        if user_requesting_detailed:
            session_data['variables']['detail_preference'] = 'detailed'
        elif user_requesting_summary:
            session_data['variables']['detail_preference'] = 'summary'

        # Use saved preference if available, otherwise default to detailed
        saved_preference = session_data['variables'].get('detail_preference', 'detailed')

        # Answer directly with the preference (no asking)
        prompt = DOCUMENT_QUERY_PROMPT.format(
            current_message=current_message,
            saved_preference=saved_preference,
            excerpts_text=excerpts_text
        )

    if history_text:
        prompt += history_text

    prompt += f"Current question: {current_message}"
    return prompt
