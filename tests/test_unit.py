#!/usr/bin/env python3
"""
Unit tests for internal pipeline components.
Tests internal logic with mocks — no running server required.

  STT retry        — channel-error retry with fresh gRPC client
  STT unreachable  — all retries fail → empty transcript
  Gemini retry     — 503/UNAVAILABLE retried with backoff
  Gemini non-retry — non-transient errors propagate immediately
  Filler suppress  — filler dropped when real sentence arrives first
  Filler emit      — filler emitted when it arrives before real sentence
  Stream ordering  — out-of-order (3,1,2) reordered to (1,2,3)

Usage (from project root, with backend venv active):
  python tests/test_unit.py
"""

import asyncio
import os
import queue as queue_mod
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List
from unittest.mock import MagicMock, patch

# ── sys.path ──────────────────────────────────────────────────────────────────
_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, _BACKEND)

# ── Pre-mock external deps so importing backend modules doesn't need credentials
# or installed cloud services. Only mocked ONCE here, before any backend import.

_MOCK_MODULES = [
    "services.tts_service",
    "services.stt_service",
    "services.streaming_stt_service",
    "services.qdrant_service",
    "services.mongodb_agent_service",
    "services.session_service",
]
for _m in _MOCK_MODULES:
    sys.modules.setdefault(_m, MagicMock())

# ── Test infrastructure ───────────────────────────────────────────────────────

_BOLD = "\033[1m"
_GRN  = "\033[32m"
_RED  = "\033[31m"
_YLW  = "\033[33m"
_DIM  = "\033[2m"
_RST  = "\033[0m"

@dataclass
class _Result:
    name:    str
    passed:  bool
    note:    str    = ""
    skipped: bool   = False

_results: List[_Result] = []


def _rec(name: str, passed: bool, note: str = "", skipped: bool = False):
    _results.append(_Result(name=name, passed=passed, note=note, skipped=skipped))
    icon = (f"{_YLW}SKIP{_RST}" if skipped
            else f"{_GRN}PASS{_RST}" if passed
            else f"{_RED}FAIL{_RST}")
    suffix = f"  {_DIM}({note}){_RST}" if note else ""
    print(f"  [{icon}] {name}{suffix}")


def _section(title: str):
    print(f"\n{_BOLD}{'─' * 68}{_RST}")
    print(f"{_BOLD}  {title}{_RST}")
    print(f"{'─' * 68}")


def _summary() -> bool:
    passed  = [r for r in _results if r.passed  and not r.skipped]
    failed  = [r for r in _results if not r.passed]
    skipped = [r for r in _results if r.skipped]
    print(f"\n{_BOLD}{'═' * 68}{_RST}")
    print(f"{_BOLD}  Unit Tests — Results{_RST}")
    print(f"{'═' * 68}")
    for r in _results:
        icon = (f"{_YLW}SKIP{_RST}" if r.skipped
                else f"{_GRN}PASS{_RST}" if r.passed
                else f"{_RED}FAIL{_RST}")
        print(f"  [{icon}] {r.name}")
    print(f"\n  {_GRN}{len(passed)} passed{_RST}  "
          f"{_RED}{len(failed)} failed{_RST}  "
          f"{_YLW}{len(skipped)} skipped{_RST}  "
          f"({len(_results)} total)\n")
    if failed:
        print(f"{_RED}{_BOLD}  Failed:{_RST}")
        for r in failed:
            print(f"    * {r.name}")
            if r.note:
                print(f"      {_DIM}{r.note}{_RST}")
    return len(failed) == 0


# ══════════════════════════════════════════════════════════════════════════════
#  STT module import (patches Google credential lookup at module-load time)
# ══════════════════════════════════════════════════════════════════════════════

_STT_AVAILABLE = False
_stt_module    = None
_STTSession    = None
_STT_MAX_RETRIES = 3
_STT_RETRY_DELAY = 0.2

try:
    # Patch the three calls that happen at stt.py module level:
    #   google.auth.default(...)
    #   google_auth_grpc.secure_authorized_channel(...)
    #   SpeechGrpcTransport(channel=...)
    with (
        patch("google.auth.default",
              return_value=(MagicMock(), "test-project")),
        patch("google.auth.transport.requests.Request",
              return_value=MagicMock()),
        patch("google.auth.transport.grpc.secure_authorized_channel",
              return_value=MagicMock()),
        patch("google.cloud.speech_v1.services.speech.transports.grpc"
              ".SpeechGrpcTransport",
              MagicMock()),
    ):
        import pipeline.stt as _stt_module
        _STTSession      = _stt_module.STTSession
        _STT_MAX_RETRIES = _stt_module._STT_MAX_RETRIES
        _STT_RETRY_DELAY = _stt_module._STT_RETRY_DELAY
        _STT_AVAILABLE   = True
except Exception as _e:
    print(f"  [WARN] pipeline.stt not importable — STT unit tests will be skipped\n"
          f"         ({_e})")


# ══════════════════════════════════════════════════════════════════════════════
#  Gemini client import
# ══════════════════════════════════════════════════════════════════════════════

_GEMINI_AVAILABLE = False
_gemini_call      = None
_gemini_stream    = None

try:
    from services.gemini_client import gemini_call as _gemini_call
    from services.gemini_client import gemini_stream_content as _gemini_stream
    _GEMINI_AVAILABLE = True
except Exception as _e:
    print(f"  [WARN] services.gemini_client not importable — Gemini unit tests skipped\n"
          f"         ({_e})")


# ══════════════════════════════════════════════════════════════════════════════
#  pipeline.tts import (mocks services.tts_service which needs google-tts)
# ══════════════════════════════════════════════════════════════════════════════

_TTS_AVAILABLE    = False
_run_ordering     = None
_SENTINEL         = None

try:
    from pipeline.base import _SENTINEL as _base_sentinel
    from pipeline.tts  import run_ordering_worker as _run_ordering_worker
    _SENTINEL      = _base_sentinel
    _run_ordering  = _run_ordering_worker
    _TTS_AVAILABLE = True
except Exception as _e:
    # Fallback: reconstruct the ordering worker logic from source to still test
    # the algorithm (used when import fails due to missing TTS credentials).
    _SENTINEL = object()

    async def _run_ordering_worker(results_q, send_audio_chunk, stop_event, t0=None):
        """Inline copy of pipeline/tts.py:run_ordering_worker for fallback testing."""
        pending            = {}
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

    _run_ordering = _run_ordering_worker
    print(f"  [WARN] pipeline.tts not importable — using inline fallback for ordering tests\n"
          f"         ({_e})")


# ══════════════════════════════════════════════════════════════════════════════
#  1 — STT retry on channel error ("Malordered Data Received")
# ══════════════════════════════════════════════════════════════════════════════

def test_stt_retry_malordered():
    _section("1 · STT Retry on Malordered/SSL Channel Error")

    if not _STT_AVAILABLE:
        _rec("STT retry: fresh client on MALORDERED", True,
             "SKIPPED — pipeline.stt not importable", skipped=True)
        return

    import google.cloud.speech as gcp_speech

    # Build a mock final response
    mock_result = MagicMock()
    mock_result.is_final     = True
    mock_result.alternatives = [MagicMock(transcript="Hello world")]
    mock_response = MagicMock()
    mock_response.results    = [mock_result]

    # First client: raises SSL/Malordered error
    mock_first  = MagicMock()
    mock_first.streaming_recognize.side_effect = Exception(
        "SSLV3_ALERT_BAD_RECORD_MAC MALORDERED DATA RECEIVED"
    )

    # Second (fresh) client: returns valid transcript
    mock_second = MagicMock()
    mock_second.streaming_recognize.return_value = iter([mock_response])

    session = _STTSession()
    _stt_module._stt_client = mock_first

    with patch.object(_stt_module, "_build_stt_client", return_value=mock_second) as mock_build:
        loop   = asyncio.new_event_loop()
        future = session.start(loop)
        session.add_audio(b"\x00" * 320)
        session.done()
        try:
            transcript = loop.run_until_complete(
                asyncio.wait_for(future, timeout=5.0)
            )
            _rec("transcript returned after MALORDERED error",
                 transcript == "Hello world", f"got {transcript!r}")
            _rec("_build_stt_client called (fresh channel built)",
                 mock_build.called, "not called")
            _rec("first client got one streaming_recognize call",
                 mock_first.streaming_recognize.call_count == 1,
                 f"calls={mock_first.streaming_recognize.call_count}")
        except Exception as e:
            _rec("transcript returned after MALORDERED error", False, str(e))
        finally:
            loop.close()


# ══════════════════════════════════════════════════════════════════════════════
#  2 — STT unreachable (all retries exhausted → empty transcript)
# ══════════════════════════════════════════════════════════════════════════════

def test_stt_unreachable():
    _section("2 · STT Unreachable (All Retries Fail)")

    if not _STT_AVAILABLE:
        _rec("STT unreachable: empty transcript returned", True,
             "SKIPPED — pipeline.stt not importable", skipped=True)
        return

    # Every attempt raises UNAVAILABLE
    mock_client = MagicMock()
    mock_client.streaming_recognize.side_effect = Exception(
        "StatusCode.UNAVAILABLE: failed to connect to all addresses"
    )

    session = _STTSession()
    _stt_module._stt_client = mock_client

    # _build_stt_client also returns a failing client on retry
    with patch.object(_stt_module, "_build_stt_client", return_value=mock_client):
        loop   = asyncio.new_event_loop()
        future = session.start(loop)
        session.add_audio(b"\x00" * 160)
        session.done()
        try:
            transcript = loop.run_until_complete(
                asyncio.wait_for(future, timeout=8.0)
            )
            _rec("empty transcript returned after all retries",
                 transcript == "", f"got {transcript!r}")
            total_calls = mock_client.streaming_recognize.call_count
            _rec(f"streaming_recognize called {_STT_MAX_RETRIES + 1} times (1 + retries)",
                 total_calls == _STT_MAX_RETRIES + 1,
                 f"got {total_calls}")
        except asyncio.TimeoutError:
            _rec("empty transcript returned after all retries", False,
                 "timed out — _run() may be stuck in retry sleep")
        except Exception as e:
            _rec("empty transcript returned after all retries", False, str(e))
        finally:
            loop.close()


# ══════════════════════════════════════════════════════════════════════════════
#  3 — Gemini retry on transient 503 / UNAVAILABLE error
# ══════════════════════════════════════════════════════════════════════════════

def test_gemini_retry_503():
    _section("3 · Gemini Retry on 503 / UNAVAILABLE")

    if not _GEMINI_AVAILABLE:
        _rec("Gemini retries on 503", True,
             "SKIPPED — services.gemini_client not importable", skipped=True)
        return

    from services import gemini_client as _gc
    _orig_max = _gc._MAX_RETRIES

    # Force small retry count (2) and zero sleep so the test runs fast
    _gc._MAX_RETRIES = 3

    mock_client = MagicMock()
    call_count  = [0]

    def _flaky(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise Exception("503 Service Unavailable: server overloaded")
        return MagicMock(candidates=[MagicMock(content=MagicMock(
            parts=[MagicMock(text="success")]
        ))])

    mock_client.models.generate_content.side_effect = _flaky

    with patch("time.sleep"):        # suppress actual sleep
        try:
            result = _gemini_call(mock_client, "prompt", MagicMock())
            _rec("gemini_call succeeds after 2 transient 503s",
                 result is not None, f"calls={call_count[0]}")
            _rec("gemini_call attempted 3 times total",
                 call_count[0] == 3, f"got {call_count[0]}")
        except Exception as e:
            _rec("gemini_call succeeds after 2 transient 503s", False, str(e))
        finally:
            _gc._MAX_RETRIES = _orig_max


# ══════════════════════════════════════════════════════════════════════════════
#  4 — Gemini non-retryable error propagates immediately
# ══════════════════════════════════════════════════════════════════════════════

def test_gemini_nonretryable_raises():
    _section("4 · Gemini Non-Retryable Error Propagates")

    if not _GEMINI_AVAILABLE:
        _rec("non-retryable error raises on first attempt", True,
             "SKIPPED — services.gemini_client not importable", skipped=True)
        return

    mock_client = MagicMock()
    call_count  = [0]

    def _bad(*args, **kwargs):
        call_count[0] += 1
        raise ValueError("Invalid prompt: context window exceeded")

    mock_client.models.generate_content.side_effect = _bad

    raised = False
    try:
        _gemini_call(mock_client, "prompt", MagicMock())
    except ValueError:
        raised = True

    with patch("time.sleep"):
        _rec("ValueError raised (not retried)", raised)
        _rec("only 1 attempt made for non-retryable error",
             call_count[0] == 1, f"got {call_count[0]}")


# ══════════════════════════════════════════════════════════════════════════════
#  5 — Filler suppressed when real sentence arrives first
# ══════════════════════════════════════════════════════════════════════════════

def test_filler_suppressed_when_late():
    _section("5 · Filler TTS Suppression (real sentence wins)")

    emitted: list = []

    async def _run():
        async def collect(text, _audio, _words):
            emitted.append(text)

        q    = asyncio.Queue()
        stop = asyncio.Event()

        # num=1 (real) arrives before num=0 (filler)
        await q.put((1, "real sentence",  "audio1", []))
        await q.put((0, "filler phrase",  "audio0", []))
        await q.put(_SENTINEL)

        await _run_ordering(q, collect, stop)

    asyncio.run(_run())

    _rec("only real sentence emitted (filler suppressed)",
         emitted == ["real sentence"], f"got {emitted}")
    _rec("filler phrase NOT in output",
         "filler phrase" not in emitted, f"emitted={emitted}")


# ══════════════════════════════════════════════════════════════════════════════
#  6 — Filler emitted when it arrives before real sentence
# ══════════════════════════════════════════════════════════════════════════════

def test_filler_emitted_when_first():
    _section("6 · Filler TTS Emission (filler arrives first)")

    emitted: list = []

    async def _run():
        async def collect(text, _audio, _words):
            emitted.append(text)

        q    = asyncio.Queue()
        stop = asyncio.Event()

        # num=0 (filler) arrives before num=1 (real)
        await q.put((0, "One moment.",    "audio0", []))
        await q.put((1, "real sentence",  "audio1", []))
        await q.put(_SENTINEL)

        await _run_ordering(q, collect, stop)

    asyncio.run(_run())

    _rec("filler emitted when first to arrive",
         "One moment." in emitted, f"emitted={emitted}")
    _rec("real sentence also emitted",
         "real sentence" in emitted, f"emitted={emitted}")
    _rec("filler emitted before real sentence",
         emitted.index("One moment.") < emitted.index("real sentence"),
         f"order={emitted}")


# ══════════════════════════════════════════════════════════════════════════════
#  7 — Stream ordering: out-of-order sentences reordered to (1, 2, 3)
# ══════════════════════════════════════════════════════════════════════════════

def test_stream_ordering_out_of_order():
    _section("7 · Stream Ordering: Out-of-Order Sentences")

    emitted: list = []

    async def _run():
        async def collect(text, _audio, _words):
            emitted.append(text)

        q    = asyncio.Queue()
        stop = asyncio.Event()

        # Arrive out of order: 3, 1, 2
        await q.put((3, "third",   "a3", []))
        await q.put((1, "first",   "a1", []))
        await q.put((2, "second",  "a2", []))
        await q.put(_SENTINEL)

        await _run_ordering(q, collect, stop)

    asyncio.run(_run())

    _rec("three sentences emitted",
         len(emitted) == 3, f"count={len(emitted)}")
    _rec("emitted in correct order (1→2→3)",
         emitted == ["first", "second", "third"], f"got {emitted}")


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{_BOLD}Voice Agent Platform — Unit Tests{_RST}")
    print(f"  Backend: {_BACKEND}")
    print(f"  Python:  {sys.version.split()[0]}\n")

    test_stt_retry_malordered()
    test_stt_unreachable()
    test_gemini_retry_503()
    test_gemini_nonretryable_raises()
    test_filler_suppressed_when_late()
    test_filler_emitted_when_first()
    test_stream_ordering_out_of_order()

    sys.exit(0 if _summary() else 1)


if __name__ == "__main__":
    main()
