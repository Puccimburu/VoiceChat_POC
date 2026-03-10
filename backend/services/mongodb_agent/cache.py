"""Redis LLM-DB answer cache helpers."""
import logging

from constants import LLM_DB_CACHE_TTL

logger = logging.getLogger("ws_gateway")


def _llm_db_cache_get(db_key: str, speech: str) -> str | None:
    try:
        from services.session_service import _get_redis
        key = f"llmdb:{db_key}:{speech.lower().strip()}"
        return _get_redis().get(key)
    except Exception:
        return None


def _llm_db_cache_set(db_key: str, speech: str, answer: str):
    try:
        from services.session_service import _get_redis
        key = f"llmdb:{db_key}:{speech.lower().strip()}"
        _get_redis().setex(key, LLM_DB_CACHE_TTL, answer)
    except Exception:
        pass
