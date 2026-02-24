import asyncio
import logging

from .base import _executor, _SENTINEL
from services.tts_service import synthesize_sentence_with_timing

logger = logging.getLogger("ws_gateway")


async def dispatch_tts(
    text: str, voice: str, num: int,
    results_q: asyncio.Queue, stop_event: asyncio.Event,
):
    if stop_event.is_set():
        return
    loop = asyncio.get_event_loop()
    try:
        audio_b64, words = await loop.run_in_executor(
            _executor, lambda: synthesize_sentence_with_timing(text, voice)
        )
        if not stop_event.is_set():
            await results_q.put((num, text, audio_b64, words))
    except Exception as e:
        logger.error(f"[TTS] synthesis error num={num}: {e}")


async def run_ordering_worker(
    results_q: asyncio.Queue,
    send_audio_chunk,
    stop_event: asyncio.Event,
):
    """Emit TTS results in ascending num order. Filler (num=0) only before first real sentence."""
    pending: dict = {}
    next_to_emit       = 1
    filler_emitted     = False
    first_real_arrived = False

    while True:
        if stop_event.is_set():
            return
        try:
            result = await asyncio.wait_for(results_q.get(), timeout=0.1)
        except asyncio.TimeoutError:
            continue

        if result is _SENTINEL:
            return

        num, text, audio, words = result

        if num == 0:
            if not first_real_arrived and not filler_emitted:
                await send_audio_chunk(text, audio, words)
                filler_emitted = True
            continue

        if num == 1:
            first_real_arrived = True

        pending[num] = (text, audio, words)
        while next_to_emit in pending:
            if stop_event.is_set():
                return
            t, a, w = pending.pop(next_to_emit)
            await send_audio_chunk(t, a, w)
            next_to_emit += 1
