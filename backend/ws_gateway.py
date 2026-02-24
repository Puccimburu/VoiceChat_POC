"""
ws_gateway.py — WebSocket gateway :8080, static file server :8081.
No Go / gRPC needed — all AI services imported directly.
"""

import asyncio
import base64
import json
import logging
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

try:
    import websockets
    import websockets.exceptions
except ImportError:
    raise ImportError("Run: pip install websockets")

from services.session_service import get_or_create_session
from services.qdrant_service import get_document_list
from services.security_service import check_origin_allowed
from pipeline.base import _executor
from pipeline.stt import STTSession
from pipeline.llm import run_llm_pipeline
from pipeline.agent import run_agent_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ws_gateway")


class ClientState:
    def __init__(self, ws, origin: str):
        self.ws           = ws
        self.origin       = origin
        self.authorized   = False
        self.api_key      = ""
        self.session_id   = ""
        self.voice        = "en-US-Neural2-J"
        self.mode         = "general"
        self.selected_doc = "all"
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
                await self.send_error(
                    "not authenticated — send {type:'auth', data:{api_key:'...'}} first"
                )
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
        api_key = data.get("api_key", "")
        if not api_key:
            await self.send_error("auth must include api_key")
            return
        loop    = asyncio.get_event_loop()
        allowed = await loop.run_in_executor(
            _executor, lambda: check_origin_allowed(api_key, self.origin)
        )
        if not allowed:
            await self.send_error("auth failed: origin not allowed")
            return
        session_id, _ = await loop.run_in_executor(
            _executor, lambda: get_or_create_session(data.get("session_id") or None)
        )
        self.api_key    = api_key
        self.session_id = session_id
        self.authorized = True
        logger.info(f"[ws] authenticated api_key={api_key!r} session={session_id}")
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
        self.voice        = data.get("voice", "en-US-Neural2-J") or "en-US-Neural2-J"
        self.mode         = data.get("mode",  "general")          or "general"
        self.selected_doc = data.get("selected_document", "all")  or "all"
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

        stop_event   = self._new_stop_event()
        session_id   = self.session_id
        api_key      = self.api_key
        voice        = self.voice
        mode         = self.mode
        selected_doc = self.selected_doc

        async def _run_pipeline():
            transcript = await stt._transcript_future
            if not transcript or not transcript.strip():
                logger.info(f"[ws] [{session_id[:8]}] empty transcript")
                await self.send_complete()
                return
            logger.info(f"[ws] [{session_id[:8]}] transcript: {transcript!r}")
            if mode == "agent":
                await run_agent_pipeline(
                    transcript, session_id, api_key, voice,
                    self.send_audio_chunk, self.send_conv_pair,
                    self.send_complete, self.send_error,
                    stop_event,
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


async def _ws_handler(websocket):
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


_WIDGET_BUILD_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'build'
)
_STATIC_FILES = {
    b'/chat-widget.js': os.path.join(_WIDGET_BUILD_DIR, 'chat-widget.js'),
    b'/widget.js':      os.path.join(_WIDGET_BUILD_DIR, 'widget.js'),
}

async def _handle_static(reader, writer):
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if line in (b'\r\n', b'\n', b''):
                break
        method, path, *_ = (request_line.decode(errors='replace').split() + ['', ''])
        path_bytes = path.split('?')[0].encode()
        if path_bytes in _STATIC_FILES:
            try:
                with open(_STATIC_FILES[path_bytes], 'rb') as f:
                    body = f.read()
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/javascript\r\n"
                    b"Access-Control-Allow-Origin: *\r\n"
                    b"Cache-Control: no-cache\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + body
                )
            except FileNotFoundError:
                response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
        else:
            response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def main():
    logger.info("[ws_gateway] WebSocket gateway on ws://0.0.0.0:8080/ws")
    logger.info("[ws_gateway] Static file server on http://0.0.0.0:8081")
    static_server = await asyncio.start_server(_handle_static, "0.0.0.0", 8081)
    async with websockets.serve(_ws_handler, "0.0.0.0", 8080):
        async with static_server:
            await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
