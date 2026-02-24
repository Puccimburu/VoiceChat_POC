import asyncio
import logging

from .base import _executor, _SENTINEL
from .helpers import is_short_greeting, pick_filler, extract_sentences
from .tts import dispatch_tts, run_ordering_worker
from services.session_service import (
    get_or_create_session,
    add_to_conversation_history,
    save_session,
    build_context_prompt,
    build_rag_prompt,
)
from services.qdrant_service import voice_search

logger = logging.getLogger("ws_gateway")


def _get_llm_stream(prompt: str):
    from services.llm_service import generate_response_stream
    return generate_response_stream(prompt)


async def run_llm_pipeline(
    transcript: str, session_id: str, mode: str, voice: str, selected_doc: str,
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
        filler = (
            "Let me check the document for you."
            if mode == "document" else pick_filler(transcript)
        )
        tts_tasks.append(asyncio.create_task(
            dispatch_tts(filler, voice, 0, results_q, stop_event)
        ))

    rag_results = []
    if mode == "document":
        doc_filter = selected_doc if selected_doc not in ("all", "", None) else None
        try:
            rag_results = await loop.run_in_executor(
                _executor, lambda: voice_search(transcript, document_filter=doc_filter),
            )
        except Exception as e:
            logger.error(f"[LLM] VoiceSearch error: {e}")

    try:
        _, session_data = await loop.run_in_executor(
            _executor, lambda: get_or_create_session(session_id)
        )
        if mode == "document" and rag_results:
            prompt = build_rag_prompt(session_data, transcript, rag_results)
            await loop.run_in_executor(_executor, lambda: save_session(session_id, session_data))
        else:
            prompt = build_context_prompt(session_data, transcript)
    except Exception as e:
        logger.error(f"[LLM] prompt build error: {e}")
        await send_error(f"Failed to build prompt: {e}")
        await results_q.put(_SENTINEL)
        await ordering_task
        await send_complete()
        return

    token_q: asyncio.Queue = asyncio.Queue()

    def _stream_llm():
        try:
            for chunk in _get_llm_stream(prompt):
                try:
                    text = chunk.text
                except (ValueError, AttributeError):
                    continue
                if text:
                    loop.call_soon_threadsafe(token_q.put_nowait, text)
        except Exception as e:
            logger.error(f"[LLM] stream error: {e}")
        finally:
            loop.call_soon_threadsafe(token_q.put_nowait, None)

    loop.run_in_executor(_executor, _stream_llm)

    sentence_buf   = ""
    full_response  = ""
    sentence_count = 0

    while True:
        if stop_event.is_set():
            break
        token = await token_q.get()
        if token is None:
            break
        sentence_buf  += token
        full_response += token
        sentences, sentence_buf = extract_sentences(sentence_buf)
        for s in sentences:
            if s.strip():
                sentence_count += 1
                tts_tasks.append(asyncio.create_task(
                    dispatch_tts(s, voice, sentence_count, results_q, stop_event)
                ))

    if sentence_buf.strip() and not stop_event.is_set():
        sentence_count += 1
        tts_tasks.append(asyncio.create_task(
            dispatch_tts(sentence_buf, voice, sentence_count, results_q, stop_event)
        ))

    await asyncio.gather(*tts_tasks, return_exceptions=True)
    await results_q.put(_SENTINEL)
    await ordering_task

    if full_response.strip() and not stop_event.is_set():
        response = full_response.strip()
        await loop.run_in_executor(
            _executor,
            lambda: add_to_conversation_history(session_id, transcript, response),
        )
        await send_conv_pair(transcript, response)

    await send_complete()
