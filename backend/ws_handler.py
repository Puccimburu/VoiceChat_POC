"""WebSocket connection handler and voice pipeline orchestration."""
import asyncio
import base64
import json
import logging
import random
import time
from typing import Optional

import websockets.exceptions

from services.security_service  import check_origin_allowed
from config                     import API_KEY
from services.session_service   import get_or_create_session
from services.qdrant_service    import get_document_list
from pipeline.base              import _executor, _SENTINEL
from pipeline.stt               import STTSession
from pipeline.llm               import run_llm_pipeline
from pipeline.agent             import run_agent_pipeline
from pipeline.tts               import dispatch_tts, run_ordering_worker
from pipeline.helpers           import is_short_greeting

logger = logging.getLogger("ws_gateway")

_EARLY_FILLERS = [
    "One moment.", "Sure, one moment.", "Let me check that.",
    "Let me look into that.", "Happy to help.", "Of course.",
    "Let me think about that.", "Sure thing.", "Absolutely.",
    "Hmm, let me think.", "Let me consider that.",
]


class ClientState:
    def __init__(self, ws, origin: str):
        self.ws              = ws
        self.origin          = origin
        self.authorized      = False
        self.session_id      = ""
        self.voice           = "en-US-Neural2-J"
        self.mode            = "general"
        self.selected_doc    = "all"
        self.selected_member: dict = {}
        self.stt_session: Optional[STTSession] = None
        self._pipeline_task: Optional[asyncio.Task] = None
        self._stop_event: asyncio.Event = asyncio.Event()

    async def send(self, msg_type: str, data):
        try:
            await self.ws.send(json.dumps({"type": msg_type, "data": data}))
        except Exception:
            pass

    async def send_error(self, message: str):
        await self.send("error", {"message": message})

    async def send_audio_chunk(self, text: str, audio_b64: str, words: list):
        await self.send("audio_chunk", {
            "text":  text,
            "audio": audio_b64,
            "words": [{"word": w["word"], "time_seconds": w["time_seconds"]} for w in words],
        })

    async def send_conv_pair(self, user_query: str, llm_response: str):
        await self.send("conversation_pair", {
            "user_query":   user_query,
            "llm_response": llm_response,
        })

    async def send_complete(self):
        await self.send("stream_complete", {"status": "done"})

    def _new_stop_event(self) -> asyncio.Event:
        if self._pipeline_task and not self._pipeline_task.done():
            self._pipeline_task.cancel()
        self._stop_event = asyncio.Event()
        return self._stop_event

    def cancel_pipeline(self):
        self._stop_event.set()
        if self._pipeline_task and not self._pipeline_task.done():
            self._pipeline_task.cancel()
        self._pipeline_task = None

    async def handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self.send_error("invalid JSON")
            return

        msg_type = msg.get("type", "")
        data     = msg.get("data") or {}

        if not self.authorized:
            if msg_type != "auth":
                await self.send_error("not authenticated — send {type:'auth', data:{api_key:'...'}} first")
                return
            await self._handle_auth(data)
            return

        logger.info(f"[ws] [{self.session_id[:8]}] type={msg_type!r}")

        if   msg_type == "get_documents": await self._handle_get_documents()
        elif msg_type == "start_stream":  await self._handle_start_stream(data)
        elif msg_type == "stt_audio":           self._handle_stt_audio(data)
        elif msg_type == "end_speech":    await self._handle_end_speech()
        elif msg_type == "barge_in":            self._handle_barge_in()
        else:                             await self.send("echo", msg)

    async def _handle_auth(self, data: dict):
        if API_KEY and data.get("api_key") != API_KEY:
            await self.send_error("auth failed: invalid API key")
            return
        if not check_origin_allowed(self.origin):
            await self.send_error("auth failed: origin not allowed")
            return
        loop = asyncio.get_event_loop()
        session_id, _ = await loop.run_in_executor(
            _executor, lambda: get_or_create_session(data.get("session_id") or None)
        )
        self.session_id = session_id
        self.authorized = True
        logger.info(f"[ws] authenticated session={session_id}")
        await self.send("connected", {"status": "ready", "session_id": session_id})

    async def _handle_get_documents(self):
        loop = asyncio.get_event_loop()
        try:
            docs = await loop.run_in_executor(_executor, get_document_list)
            await self.send("documents_list", {"documents": docs})
        except Exception as e:
            logger.error(f"[ws] get_documents error: {e}")
            await self.send_error("failed to fetch documents")

    async def _handle_start_stream(self, data: dict):
        self.voice           = data.get("voice", "en-US-Neural2-J") or "en-US-Neural2-J"
        self.mode            = data.get("mode",  "general")          or "general"
        self.selected_doc    = data.get("selected_document", "all")  or "all"
        self.selected_member = data.get("selected_member") or {}
        if self.stt_session:
            self.stt_session.close()
        self.stt_session = STTSession()
        self.stt_session.start(asyncio.get_event_loop())
        logger.info(f"[ws] [{self.session_id[:8]}] STT started mode={self.mode} voice={self.voice}")
        await self.send("stream_started", {"session_id": self.session_id})

    def _handle_stt_audio(self, data: dict):
        if not self.stt_session:
            return
        audio_b64 = data.get("audio", "")
        if not audio_b64:
            return
        try:
            self.stt_session.add_audio(base64.b64decode(audio_b64))
        except Exception as e:
            logger.warning(f"[ws] stt_audio decode error: {e}")

    async def _handle_end_speech(self):
        if not self.stt_session:
            await self.send_complete()
            return

        stt = self.stt_session
        self.stt_session = None
        stt.done()

        stop_event      = self._new_stop_event()
        session_id      = self.session_id
        voice           = self.voice
        mode            = self.mode
        selected_doc    = self.selected_doc
        selected_member = self.selected_member

        async def _run_pipeline():
            # Create audio queue + ordering worker immediately — before STT completes.
            # Dispatch a filler early so the user hears something while Google STT
            # processes (~300-500ms). The _filler_ok flag lets us suppress it after
            # the fact if the transcript turns out to be a short greeting.
            results_q     = asyncio.Queue()
            ordering_task = asyncio.create_task(
                run_ordering_worker(results_q, self.send_audio_chunk, stop_event)
            )
            _filler_ok = [True]
            asyncio.create_task(
                dispatch_tts(random.choice(_EARLY_FILLERS), voice, 0, results_q, stop_event, _allowed=_filler_ok)
            )

            transcript = await stt._transcript_future
            if not transcript or not transcript.strip():
                logger.info(f"[ws] [{session_id[:8]}] empty transcript")
                _filler_ok[0] = False
                await results_q.put(_SENTINEL)
                await ordering_task
                await self.send_complete()
                return
            # Suppress filler for greetings — the response is fast and "One moment."
            # before "Hello! How can I help?" sounds odd.
            if is_short_greeting(transcript):
                _filler_ok[0] = False
            t0 = time.monotonic()
            logger.info(f"[ws] [{session_id[:8]}] transcript: {transcript!r}")
            if mode == "agent":
                await run_agent_pipeline(
                    transcript, session_id, voice,
                    self.send_audio_chunk, self.send_conv_pair,
                    self.send_complete, self.send_error,
                    stop_event,
                    selected_member=selected_member,
                    _results_q=results_q,
                    _ordering_task=ordering_task,
                    _t0=t0,
                )
            else:
                await run_llm_pipeline(
                    transcript, session_id, mode, voice, selected_doc,
                    self.send_audio_chunk, self.send_conv_pair,
                    self.send_complete, self.send_error,
                    stop_event,
                )

        self._pipeline_task = asyncio.create_task(_run_pipeline())

    def _handle_barge_in(self):
        logger.info(f"[ws] [{self.session_id[:8]}] barge-in — cancelling pipeline")
        self.cancel_pipeline()


async def ws_handler(websocket):
    try:
        origin = websocket.request_headers.get("Origin", "")
    except Exception:
        origin = ""
    addr = getattr(websocket, "remote_address", "unknown")
    logger.info(f"[ws] new connection from {addr}  origin={origin!r}")
    client = ClientState(websocket, origin)
    try:
        async for message in websocket:
            await client.handle_message(message)
    except websockets.exceptions.ConnectionClosedError:
        pass
    except Exception as e:
        logger.error(f"[ws] handler error: {e}", exc_info=True)
    finally:
        client.cancel_pipeline()
        if client.stt_session:
            client.stt_session.close()
        logger.info(f"[ws] connection closed from {addr}")
