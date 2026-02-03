"""Session management service"""
import uuid
from collections import deque
from datetime import datetime, timedelta
import logging
from config import SESSION_TIMEOUT, MAX_HISTORY

logger = logging.getLogger(__name__)

# In-memory session storage
sessions = {}


def get_or_create_session(session_id=None):
    """Get existing session or create new one with context storage

    Args:
        session_id: Optional session ID from cookie/header

    Returns:
        tuple: (session_id, session_data)
    """
    now = datetime.now()

    # Clean up expired sessions
    expired_sessions = [
        sid for sid, data in sessions.items()
        if now - data['last_access'] > timedelta(seconds=SESSION_TIMEOUT)
    ]
    for sid in expired_sessions:
        del sessions[sid]
        logger.info(f"🧹 Cleaned up expired session {sid}")

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
        logger.info(f"✨ Created new session {session_id}")
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

    Args:
        session_data: Session dictionary with history
        current_message: Current user message

    Returns:
        str: Formatted prompt with context
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


def get_session(session_id):
    """Get session data"""
    return sessions.get(session_id)


def delete_session(session_id):
    """Delete session"""
    if session_id in sessions:
        del sessions[session_id]
        logger.info(f" Deleted session {session_id}")
        return True
    return False
