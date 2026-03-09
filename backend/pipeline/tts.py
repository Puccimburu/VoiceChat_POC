import asyncio
import logging
import time

from .base import _executor, _SENTINEL
from services.tts_service import synthesize_sentence_with_timing

logger = logging.getLogger("ws_gateway")


async def dispatch_tts(
    text: str, voice: str, num: int,
    results_q: asyncio.Queue, stop_event: asyncio.Event,
    _allowed: list = None,
):
    """_allowed is a mutable [bool] flag — set to False to suppress queuing after TTS completes."""
    if stop_event.is_set():
        return
    loop = asyncio.get_event_loop()
    try:
        audio_b64, words = await loop.run_in_executor(
            _executor, lambda: synthesize_sentence_with_timing(text, voice)
        )
        if not stop_event.is_set() and (_allowed is None or _allowed[0]):
            await results_q.put((num, text, audio_b64, words))
    except Exception as e:
        logger.error(f"[TTS] synthesis error num={num}: {e}")


async def run_ordering_worker(
    results_q: asyncio.Queue,
    send_audio_chunk,
    stop_event: asyncio.Event,
    t0: float = None,
):
    """Emit TTS results in ascending num order. Filler (num=0) only before first real sentence."""
    pending: dict = {}
    next_to_emit       = 1
    filler_emitted     = False
    first_real_arrived = False
    first_audio_sent   = False

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
                if not first_audio_sent and t0 is not None:
                    logger.info(f"[TIMING] first audio (filler) sent: {(time.monotonic()-t0)*1000:.0f}ms since transcript")
                    first_audio_sent = True
            continue

        if num == 1:
            first_real_arrived = True

        pending[num] = (text, audio, words)
        while next_to_emit in pending:
            if stop_event.is_set():
                return
            t, a, w = pending.pop(next_to_emit)
            await send_audio_chunk(t, a, w)
            if not first_audio_sent and t0 is not None:
                logger.info(f"[TIMING] first audio (real) sent: {(time.monotonic()-t0)*1000:.0f}ms since transcript")
                first_audio_sent = True
            next_to_emit += 1
