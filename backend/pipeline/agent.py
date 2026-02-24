import asyncio
import logging
from typing import Optional

from pymongo import MongoClient

from .base import _executor, _SENTINEL
from .helpers import is_short_greeting, pick_filler, extract_sentences
from .tts import dispatch_tts, run_ordering_worker
from services.session_service import get_or_create_session, add_to_conversation_history, save_session
from services.security_service import decrypt_connection_string
from services.mongodb_agent_service import MongoDBAgent
from services.sqlite_agent_service import SQLiteAgent
from config import PLATFORM_MONGO_URI, PLATFORM_DB

logger = logging.getLogger("ws_gateway")


def _get_db_config(api_key: str) -> Optional[dict]:
    try:
        db  = MongoClient(PLATFORM_MONGO_URI)[PLATFORM_DB]
        doc = db.api_keys.find_one({"key": api_key}, {"db_config": 1})
        if not doc or not doc.get("db_config"):
            return None
        cfg = dict(doc["db_config"])
        if "connection_string" in cfg:
            cfg["connection_string"] = decrypt_connection_string(cfg["connection_string"])
        return cfg
    except Exception as e:
        logger.error(f"DB config lookup error: {e}")
        return None


async def run_agent_pipeline(
    transcript: str, session_id: str, api_key: str, voice: str,
    send_audio_chunk, send_conv_pair, send_complete, send_error,
    stop_event: asyncio.Event,
):
    loop      = asyncio.get_event_loop()
    results_q: asyncio.Queue = asyncio.Queue()
    tts_tasks = []

    ordering_task = asyncio.create_task(
        run_ordering_worker(results_q, send_audio_chunk, stop_event)
    )

    if not is_short_greeting(transcript):
        tts_tasks.append(asyncio.create_task(
            dispatch_tts(pick_filler(transcript), voice, 0, results_q, stop_event)
        ))

    def _run_agent():
        _, session_data = get_or_create_session(session_id)
        history  = session_data.get("history", [])[-4:]
        pending  = session_data.get("variables", {}).get("pending_booking")
        db_config = _get_db_config(api_key) if api_key else None
        db_type   = (db_config or {}).get("type", "mongodb")
        agent     = (
            SQLiteAgent(db_config=db_config)
            if db_type == "sqlite"
            else MongoDBAgent(db_config=db_config)
        )
        response_text = ""
        try:
            response_text = agent.query(transcript, history=history, pending=pending)
        finally:
            _, sd = get_or_create_session(session_id)
            next_pending = getattr(agent, "_next_pending", None)
            if next_pending is not None:
                sd.setdefault("variables", {})["pending_booking"] = next_pending
            else:
                sd.setdefault("variables", {}).pop("pending_booking", None)
            save_session(session_id, sd)
            if response_text:
                add_to_conversation_history(session_id, transcript, response_text)
            agent.close()
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

    sentence_count = 0
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
    await results_q.put(_SENTINEL)
    await ordering_task

    if response_text.strip():
        await send_conv_pair(transcript, response_text.strip())
    await send_complete()
