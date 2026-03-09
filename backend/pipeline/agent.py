import asyncio
import logging
import re
import time
from typing import Optional

from pymongo import MongoClient

# Normalize common STT garbling / alternate phrasings into canonical form
# before the transcript reaches the agent.

# STT garbling of "enroll me": "roll me", "and roll me", "brought me", "bro me" etc.
_ENROLL_RE = re.compile(
    r'\b(?:(?:and|in|yes[,\s]+and)\s+)?'
    r'(?:enrolled?|roll(?:ed)?|brought|bro(?:ught)?|brung)\s+me\b',
    re.IGNORECASE,
)

# "me a slot/spot/session/place/space in/for/on"  → "enroll me in"
# "for me, a slot in"                             → "enroll me in"
# "book/get/give/take for me a slot in"           → "enroll me in"
# "book me a session on Sunday"                   → "enroll me in"
_SLOT_RE = re.compile(
    r'\b(?:(?:book|get|give|take)\s+(?:for\s+)?me|(?:for\s+)?me)[,\s]+a\s+'
    r'(?:slot|spot|sport|session|place|space|s[lp]ot|sess\w*)\s+(?:in|for|to|on)\b',
    re.IGNORECASE,
)

# "will you book me a session" / "can you book me a slot" → "enroll me in"
_WILL_BOOK_RE = re.compile(
    r'\b(?:(?:will|can|could|would)\s+you\s+)?book\s+me\s+a\s+'
    r'(?:slot|spot|session|place|space)\b',
    re.IGNORECASE,
)

# "book me in/into/for the X class" → "enroll me in the X class"
# "sign me up for the X class"       → "enroll me in the X class"
# "add me to the X class"            → "enroll me in the X class"
_BOOK_ME_RE = re.compile(
    r'\b(?:book\s+me\s+(?:in(?:to)?|for)|sign\s+me\s+up\s+for|add\s+me\s+to)\b',
    re.IGNORECASE,
)

# After enrollment normalization, fix the preposition: "enroll me to/for X" → "enroll me in X"
_ENROLL_PREP_RE = re.compile(
    r'\benroll\s+me\s+(?:to|for)\b',
    re.IGNORECASE,
)

# STT garbling of "cancel": "console", "council", "counsel" etc.
_CANCEL_RE = re.compile(
    r'\b(?:console|counsel|council|ken\s*sel|can\s*soul)\b',
    re.IGNORECASE,
)


def _normalize_transcript(text: str) -> str:
    original = text
    text = _CANCEL_RE.sub("cancel", text)
    text = _ENROLL_RE.sub("enroll me", text)
    text = _SLOT_RE.sub("enroll me in", text)
    text = _WILL_BOOK_RE.sub("enroll me in", text)
    text = _BOOK_ME_RE.sub("enroll me in", text)
    text = _ENROLL_PREP_RE.sub("enroll me in", text)
    if text != original:
        logger.info(f"[Normalize] '{original}' -> '{text}'"  )
    return text

from .base import _executor, _SENTINEL
from .helpers import is_short_greeting, pick_filler, extract_sentences, pick_greeting_reply
from .tts import dispatch_tts, run_ordering_worker
from services.session_service import get_or_create_session, add_to_conversation_history, save_session
from services.security_service import decrypt_connection_string
from services.mongodb_agent_service import MongoDBAgent
from services.sqlite_agent_service import SQLiteAgent
from config import PLATFORM_MONGO_URI, PLATFORM_DB

logger = logging.getLogger("ws_gateway")

# One MongoClient per unique connection string — shared across all sessions/tenants
# that use the same database, avoiding a new TLS handshake on every voice turn.
_client_cache: dict = {}

def _get_mongo_client(conn_str: str) -> MongoClient:
    if conn_str not in _client_cache:
        _client_cache[conn_str] = MongoClient(conn_str)
    return _client_cache[conn_str]

# Cache db_config per api_key — avoids a find_one round-trip to Atlas on every voice turn.
_db_config_cache: dict = {}
_DB_CONFIG_TTL = 3600  # seconds (1 hour — api_keys rarely change)

def _get_db_config(api_key: str) -> Optional[dict]:
    now = time.monotonic()
    cached = _db_config_cache.get(api_key)
    if cached and (now - cached["ts"]) < _DB_CONFIG_TTL:
        return cached["cfg"]
    try:
        platform_client = _get_mongo_client(PLATFORM_MONGO_URI)
        doc = platform_client[PLATFORM_DB].api_keys.find_one({"key": api_key}, {"db_config": 1})
        cfg = None
        if doc and doc.get("db_config"):
            cfg = dict(doc["db_config"])
            if "connection_string" in cfg:
                cfg["connection_string"] = decrypt_connection_string(cfg["connection_string"])
        _db_config_cache[api_key] = {"cfg": cfg, "ts": now}
        return cfg
    except Exception as e:
        logger.error(f"DB config lookup error: {e}")
        return None


def prewarm_connections():
    """Pre-warm MongoDB connections at startup so the first voice turn has no cold-start latency."""
    try:
        t = time.monotonic()
        platform_client = _get_mongo_client(PLATFORM_MONGO_URI)
        platform_client.admin.command("ping")
        logger.info(f"[prewarm] platform Atlas ping OK ({(time.monotonic()-t)*1000:.0f}ms)")

        now = time.monotonic()
        docs = list(platform_client[PLATFORM_DB].api_keys.find(
            {"active": True}, {"key": 1, "db_config": 1}
        ))
        for doc in docs:
            key = doc.get("key")
            cfg = doc.get("db_config")
            if not key or not cfg:
                continue
            try:
                cfg = dict(cfg)
                if "connection_string" in cfg:
                    cfg["connection_string"] = decrypt_connection_string(cfg["connection_string"])
                _db_config_cache[key] = {"cfg": cfg, "ts": now}
                conn_str = cfg.get("connection_string", PLATFORM_MONGO_URI)
                if cfg.get("type", "mongodb") == "mongodb":
                    client = _get_mongo_client(conn_str)
                    client.admin.command("ping")
                    logger.info(f"[prewarm] customer DB ping OK for key={key[:8]}...")
            except Exception as e:
                logger.warning(f"[prewarm] customer DB ping failed for key={key[:8]}...: {e}")
    except Exception as e:
        logger.warning(f"[prewarm] platform ping failed: {e}")


async def run_agent_pipeline(
    transcript: str, session_id: str, api_key: str, voice: str,
    send_audio_chunk, send_conv_pair, send_complete, send_error,
    stop_event: asyncio.Event,
    selected_member: dict = None,
    _results_q: asyncio.Queue = None,
    _ordering_task = None,
    _t0: float = None,
):
    loop      = asyncio.get_event_loop()
    tts_tasks = []
    t0 = _t0 if _t0 is not None else time.monotonic()

    # Accept a pre-created queue/worker from the caller (early filler optimisation).
    # If not provided, create them here as before.
    if _results_q is not None:
        results_q     = _results_q
        ordering_task = _ordering_task
        # Filler was already dispatched by the caller as num=0; real sentences start at 1.
        _next_tts_num = 1
    else:
        results_q     = asyncio.Queue()
        ordering_task = asyncio.create_task(
            run_ordering_worker(results_q, send_audio_chunk, stop_event, t0=t0)
        )
        if not is_short_greeting(transcript):
            tts_tasks.append(asyncio.create_task(
                dispatch_tts(pick_filler(transcript), voice, 0, results_q, stop_event)
            ))
        _next_tts_num = 1

    # Streaming: sentences dispatched inline from executor thread via run_coroutine_threadsafe
    _streaming_futures = []  # concurrent.futures.Future objects

    def _on_sentence(text, num):
        if stop_event.is_set():
            return
        fut = asyncio.run_coroutine_threadsafe(
            dispatch_tts(text, voice, num, results_q, stop_event), loop
        )
        _streaming_futures.append(fut)

    def _run_agent():
        _, session_data = get_or_create_session(session_id)
        history  = session_data.get("history", [])[-4:]
        pending  = session_data.get("variables", {}).get("pending_booking")
        t_db = time.monotonic()
        db_config = _get_db_config(api_key) if api_key else None
        logger.info(f"[TIMING] db_config: {(time.monotonic()-t_db)*1000:.0f}ms")
        db_type   = (db_config or {}).get("type", "mongodb")
        if db_type == "sqlite":
            agent = SQLiteAgent(db_config=db_config)
        else:
            # Reuse a cached MongoClient for this connection string so the Atlas
            # TLS handshake only happens once per unique tenant database endpoint.
            # Each request gets a fresh MongoDBAgent (fresh per-query state) that
            # wraps the shared client — safe for concurrent multi-tenant sessions.
            conn_str = (db_config or {}).get("connection_string", PLATFORM_MONGO_URI)
            agent = MongoDBAgent(db_config=db_config, mongo_client=_get_mongo_client(conn_str))
        # Normalize STT garbling before the agent sees the transcript
        normalized = _normalize_transcript(transcript)
        # Prepend selected member context so the agent knows who is speaking
        query = normalized
        if selected_member and selected_member.get("name"):
            m = selected_member
            ctx = (f"[CURRENT USER: name={m.get('name')}, "
                   f"member_id={m.get('member_id', '')}, "
                   f"membership={m.get('membership_type', '')}] ")
            query = ctx + normalized
        # Fast-path: greetings bypass the LLM entirely (0 Gemini calls)
        if is_short_greeting(normalized) and not pending:
            logger.info("[Agent] greeting fast-path — skipping LLM")
            return pick_greeting_reply(normalized)

        response_text = ""
        t_llm = time.monotonic()
        try:
            response_text = agent.query(query, history=history, pending=pending,
                                        on_sentence=_on_sentence, _start_num=_next_tts_num)
            logger.info(f"[TIMING] agent.query (DB+LLM): {(time.monotonic()-t_llm)*1000:.0f}ms | total since transcript: {(time.monotonic()-t0)*1000:.0f}ms")
        finally:
            _, sd = get_or_create_session(session_id)
            next_pending = getattr(agent, "_next_pending", None)
            if next_pending is not None:
                sd.setdefault("variables", {})["pending_booking"] = next_pending
            else:
                sd.setdefault("variables", {}).pop("pending_booking", None)
            save_session(session_id, sd)
            # Only store meaningful exchanges — skip fallback/error responses so they
            # don't corrupt the conversation history and teach the model bad patterns.
            _skip = {
                "Done.",
                "I wasn't able to complete that request. Please try again.",
                "Sorry, I didn't quite catch that. Could you say that again?",
            }
            if response_text and response_text.strip() not in _skip:
                add_to_conversation_history(session_id, transcript, response_text)
            # Don't close — agent is cached and its connection is reused across turns
        return response_text

    try:
        response_text = await loop.run_in_executor(_executor, _run_agent)
    except Exception as e:
        logger.error(f"[Agent] query error: {e}")
        await send_error(f"Agent error: {e}")
        await results_q.put(_SENTINEL)
        await ordering_task
        await send_complete()
        return

    if stop_event.is_set():
        await results_q.put(_SENTINEL)
        await ordering_task
        return

    if _streaming_futures:
        # Sentences were already dispatched inline during streaming — just await them
        await asyncio.gather(
            *tts_tasks,  # includes filler (num=0) if dispatched
            *[asyncio.wrap_future(f) for f in _streaming_futures],
            return_exceptions=True,
        )
    else:
        # Batch dispatch (non-streaming path: greetings, fast-path, fallback)
        sentence_count = _next_tts_num - 1
        buf = response_text
        while True:
            sentences, buf = extract_sentences(buf)
            if not sentences:
                break
            for s in sentences:
                if s.strip():
                    sentence_count += 1
                    tts_tasks.append(asyncio.create_task(
                        dispatch_tts(s, voice, sentence_count, results_q, stop_event)
                    ))
        if buf.strip():
            sentence_count += 1
            tts_tasks.append(asyncio.create_task(
                dispatch_tts(buf, voice, sentence_count, results_q, stop_event)
            ))
        await asyncio.gather(*tts_tasks, return_exceptions=True)
    logger.info(f"[TIMING] TTS synthesized (first audio imminent): {(time.monotonic()-t0)*1000:.0f}ms since transcript")
    await results_q.put(_SENTINEL)
    await ordering_task

    if response_text.strip():
        await send_conv_pair(transcript, response_text.strip())
    await send_complete()
