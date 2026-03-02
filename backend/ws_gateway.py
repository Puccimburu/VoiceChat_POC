"""
ws_gateway.py — Single entry point for the Voice Agent Platform.

Starts three servers in one process:
  :8080  WebSocket gateway  (AI voice pipeline)
  :8081  Static file server (widget.js, VAD assets)
  :5001  REST API           (Flask, background thread)
"""

import asyncio
import base64
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
from datetime import datetime
from functools import wraps
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

try:
    import websockets
    import websockets.exceptions
except ImportError:
    raise ImportError("Run: pip install websockets")

from flask import Flask, request as flask_request, jsonify
from flask_cors import CORS
from pymongo import MongoClient, ASCENDING, DESCENDING

from services.session_service        import get_or_create_session
from services.qdrant_service         import get_document_list, upload_document
from services.security_service       import check_origin_allowed, encrypt_connection_string, decrypt_connection_string
from services.mongodb_agent_service  import MongoDBAgent
from services.sqlite_agent_service   import SQLiteAgent
from services.tts_service            import synthesize_sentence_with_timing
from pipeline.base                   import _executor
from pipeline.stt                    import STTSession
from pipeline.llm                    import run_llm_pipeline
from pipeline.agent                  import run_agent_pipeline
from config                          import PLATFORM_MONGO_URI, PLATFORM_DB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ws_gateway")

ADMIN_SECRET   = os.getenv("ADMIN_SECRET", "")
platform_client = MongoClient(PLATFORM_MONGO_URI)
platform_db     = platform_client[PLATFORM_DB]
_agent_cache: dict = {}


# ══════════════════════════════════════════════════════════════════
#  REST API  (Flask — port 5001)
# ══════════════════════════════════════════════════════════════════

flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "*"}})


# ── Auth decorators ───────────────────────────────────────────────

def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = (flask_request.headers.get("X-API-Key") or
               flask_request.headers.get("Authorization", "").replace("Bearer ", ""))
        if not key:
            return jsonify({"error": "API key required"}), 401
        doc = platform_db.api_keys.find_one({"key": key, "active": True})
        if not doc:
            return jsonify({"error": "Invalid or inactive API key"}), 403
        platform_db.api_keys.update_one(
            {"key": key},
            {"$inc": {"usage_count": 1}, "$set": {"last_used": datetime.now().isoformat()}}
        )
        flask_request.customer_id   = doc["customer_id"]
        flask_request.customer_name = doc["customer_name"]
        flask_request.db_config     = doc.get("db_config", {})
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ADMIN_SECRET:
            return f(*args, **kwargs)
        token = (flask_request.headers.get("X-Admin-Token") or
                 flask_request.headers.get("Authorization", "").replace("Bearer ", ""))
        if token != ADMIN_SECRET:
            return jsonify({"error": "Admin token required or invalid"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Helpers ───────────────────────────────────────────────────────

def _get_agent(customer_id: str, db_config: dict):
    if customer_id not in _agent_cache:
        cfg = dict(db_config)
        if cfg.get("type") == "sqlite":
            _agent_cache[customer_id] = SQLiteAgent(db_config=cfg)
        else:
            cfg["connection_string"] = decrypt_connection_string(cfg.get("connection_string", ""))
            _agent_cache[customer_id] = MongoDBAgent(db_config=cfg)
    return _agent_cache[customer_id]


def _customer_db():
    cfg = flask_request.db_config
    return MongoClient(decrypt_connection_string(cfg.get("connection_string", "")))[cfg.get("database", "")]


def _build_filter():
    reserved = {"_limit", "_fields", "_sort", "_order"}
    return {k: v for k, v in flask_request.args.items() if k not in reserved}


# ── Customer: AI agent ────────────────────────────────────────────

@flask_app.route("/api/agent/query", methods=["POST"])
@require_api_key
def agent_query():
    data         = flask_request.json or {}
    query        = data.get("query", "").strip()
    return_audio = data.get("return_audio", False)
    voice        = data.get("voice", "en-US-Neural2-J")
    history      = data.get("history")
    pending      = data.get("pending")
    if not query:
        return jsonify({"error": "query is required"}), 400
    try:
        agent  = _get_agent(flask_request.customer_id, flask_request.db_config)
        text   = agent.query(query, history=history, pending=pending)
        result = {
            "query":     query,
            "response":  text,
            "customer":  flask_request.customer_name,
            "timestamp": datetime.now().isoformat(),
            "pending":   getattr(agent, "_next_pending", None),
        }
        if return_audio:
            audio_b64, _ = synthesize_sentence_with_timing(text, voice)
            result["audio"] = audio_b64
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Customer: generic CRUD ────────────────────────────────────────

@flask_app.route("/api/data/<collection>", methods=["GET"])
@require_api_key
def data_list(collection):
    if flask_request.db_config.get("type") == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    try:
        db     = _customer_db()
        filt   = _build_filter()
        fields = flask_request.args.get("_fields", "")
        proj   = {f.strip(): 1 for f in fields.split(",") if f.strip()} if fields else {}
        proj["_id"] = 0
        limit  = min(int(flask_request.args.get("_limit", 100)), 500)
        sort   = flask_request.args.get("_sort")
        order  = DESCENDING if flask_request.args.get("_order", "asc").lower() == "desc" else ASCENDING
        cursor = db[collection].find(filt, proj).limit(limit)
        if sort:
            cursor = cursor.sort(sort, order)
        docs = list(cursor)
        return jsonify({collection: docs, "count": len(docs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/api/data/<collection>", methods=["POST"])
@require_api_key
def data_insert(collection):
    if flask_request.db_config.get("type") == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    doc = flask_request.json or {}
    if not doc:
        return jsonify({"error": "Request body required"}), 400
    try:
        db = _customer_db()
        doc.setdefault("created_at", datetime.now().isoformat())
        db[collection].insert_one(doc)
        doc.pop("_id", None)
        return jsonify(doc), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/api/data/<collection>", methods=["PATCH"])
@require_api_key
def data_update(collection):
    if flask_request.db_config.get("type") == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    body   = flask_request.json or {}
    filt   = body.get("filter") or _build_filter()
    update = body.get("update", {})
    if not filt or not update:
        return jsonify({"error": "'filter' and 'update' are required"}), 400
    try:
        res = _customer_db()[collection].update_many(filt, {"$set": update})
        return jsonify({"matched": res.matched_count, "modified": res.modified_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/api/data/<collection>", methods=["DELETE"])
@require_api_key
def data_delete(collection):
    if flask_request.db_config.get("type") == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    body = flask_request.json or {}
    filt = body.get("filter") or _build_filter()
    if not filt:
        return jsonify({"error": "filter required — pass in body or as query params"}), 400
    try:
        res = _customer_db()[collection].delete_many(filt)
        return jsonify({"deleted": res.deleted_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Admin: customer management ────────────────────────────────────

@flask_app.route("/admin/customers/register", methods=["POST"])
@require_admin
def register_customer():
    data      = flask_request.json or {}
    name      = data.get("customer_name")
    email     = data.get("customer_email")
    db_config = data.get("db_config", {})
    if not name or not email:
        return jsonify({"error": "customer_name and customer_email are required"}), 400
    db_type = db_config.get("type", "mongodb")
    if db_type == "sqlite":
        if not db_config.get("db_path"):
            return jsonify({"error": "db_config must include db_path for sqlite"}), 400
    elif not db_config.get("connection_string") or not db_config.get("database"):
        return jsonify({"error": "db_config must include connection_string and database"}), 400
    customer_id = f"CUST_{secrets.token_hex(6).upper()}"
    api_key     = f"va_{secrets.token_urlsafe(32)}"
    secured     = dict(db_config)
    if db_type != "sqlite":
        secured["connection_string"] = encrypt_connection_string(db_config["connection_string"])
    platform_db.customers.insert_one({"customer_id": customer_id, "name": name, "email": email, "created_at": datetime.now().isoformat()})
    platform_db.api_keys.insert_one({"key": api_key, "customer_id": customer_id, "customer_name": name, "db_config": secured, "active": True, "usage_count": 0, "last_used": None, "created_at": datetime.now().isoformat()})
    return jsonify({"message": "Customer registered", "customer_id": customer_id, "customer_name": name, "api_key": api_key}), 201


@flask_app.route("/admin/customers", methods=["GET"])
@require_admin
def list_customers():
    return jsonify({"customers": list(platform_db.customers.find({}, {"_id": 0})), "api_keys": list(platform_db.api_keys.find({}, {"_id": 0}))})


@flask_app.route("/admin/customers/<customer_id>/revoke", methods=["POST"])
@require_admin
def revoke_key(customer_id):
    platform_db.api_keys.update_many({"customer_id": customer_id}, {"$set": {"active": False}})
    return jsonify({"message": f"API key(s) for {customer_id} revoked"})


# ── Admin: document management ────────────────────────────────────

@flask_app.route("/admin/documents/upload", methods=["POST"])
@require_admin
def admin_upload_document():
    if "file" not in flask_request.files or not flask_request.files["file"].filename:
        return jsonify({"success": False, "message": "No file provided"}), 400
    file = flask_request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"success": False, "message": "Only PDF files are supported"}), 400
    try:
        result = upload_document(file.read(), file.filename)
        return jsonify(result), 200 if result["success"] else 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@flask_app.route("/admin/documents/list", methods=["GET"])
@require_admin
def admin_list_documents():
    try:
        docs = get_document_list()
        return jsonify({"documents": docs, "count": len(docs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/api/widget/init", methods=["GET"])
def widget_init():
    """
    Auto-discover the API key for the requesting page's origin.
    Called by the widget loader when no data-api-key is provided.
    The browser sends the Origin header automatically — it cannot be spoofed by page JS.
    """
    origin = flask_request.headers.get("Origin", "")

    def normalise(url):
        return url.lower().replace("https://", "").replace("http://", "").rstrip("/")

    origin_clean = normalise(origin)

    for doc in platform_db.api_keys.find({"active": True}, {"key": 1, "allowed_domains": 1}):
        allowed = doc.get("allowed_domains", [])
        if not allowed:
            # No domain restriction configured — matches any origin (dev / open mode)
            return jsonify({"api_key": doc["key"]})
        if origin_clean and any(normalise(d) in origin_clean for d in allowed):
            return jsonify({"api_key": doc["key"]})

    return jsonify({"error": "No API key found for this origin"}), 404


@flask_app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "Voice Agent Platform"})


@flask_app.route("/")
def index():
    return jsonify({
        "service": "Voice Agent Platform",
        "ports": {"websocket": 8080, "static": 8081, "api": 5001},
        "customer_endpoints": {
            "POST  /api/agent/query":        "Text -> AI agent response",
            "GET   /api/data/<collection>":  "List/filter (params: _fields _limit _sort _order + field=value)",
            "POST  /api/data/<collection>":  "Insert document",
            "PATCH /api/data/<collection>":  "Update — body: {filter, update}",
            "DELETE /api/data/<collection>": "Delete — body: {filter}",
        },
        "admin_endpoints": {
            "POST /admin/customers/register":    "Register customer + API key",
            "GET  /admin/customers":             "List all customers",
            "POST /admin/customers/<id>/revoke": "Revoke API key",
            "POST /admin/documents/upload":      "Upload PDF",
            "GET  /admin/documents/list":        "List documents",
        }
    })


# ══════════════════════════════════════════════════════════════════
#  WebSocket gateway  (port 8080)
# ══════════════════════════════════════════════════════════════════

class ClientState:
    def __init__(self, ws, origin: str):
        self.ws              = ws
        self.origin          = origin
        self.authorized      = False
        self.api_key         = ""
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
        api_key         = self.api_key
        voice           = self.voice
        mode            = self.mode
        selected_doc    = self.selected_doc
        selected_member = self.selected_member

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
                    selected_member=selected_member,
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


# ══════════════════════════════════════════════════════════════════
#  Static file server  (port 8081)
# ══════════════════════════════════════════════════════════════════

_WIDGET_BUILD_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'build')
_WIDGET_PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'public')
_STATIC_FILES = {
    b'/chat-widget.js':            os.path.join(_WIDGET_BUILD_DIR,  'chat-widget.js'),
    b'/widget.js':                 os.path.join(_WIDGET_BUILD_DIR,  'widget.js'),
    b'/vad.worklet.bundle.min.js': os.path.join(_WIDGET_PUBLIC_DIR, 'vad.worklet.bundle.min.js'),
    b'/silero_vad_v5.onnx':        os.path.join(_WIDGET_PUBLIC_DIR, 'silero_vad_v5.onnx'),
}

async def _handle_static(reader, writer):
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if line in (b'\r\n', b'\n', b''):
                break
        _, path, *_ = (request_line.decode(errors='replace').split() + ['', ''])
        path_bytes = path.split('?')[0].encode()
        if path_bytes in _STATIC_FILES:
            try:
                with open(_STATIC_FILES[path_bytes], 'rb') as f:
                    body = f.read()
                content_type = (
                    b"application/octet-stream" if path_bytes.endswith(b'.onnx')
                    else b"application/javascript"
                )
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: " + content_type + b"\r\n"
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


# ══════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════

async def main():
    # Start Flask REST API in a background thread
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False),
        daemon=True,
        name="flask-api",
    )
    flask_thread.start()
    logger.info("[flask] REST API on http://0.0.0.0:5001")

    logger.info("[ws_gateway] WebSocket gateway on ws://0.0.0.0:8080/ws")
    logger.info("[ws_gateway] Static file server on http://0.0.0.0:8081")
    static_server = await asyncio.start_server(_handle_static, "0.0.0.0", 8081)
    async with websockets.serve(_ws_handler, "0.0.0.0", 8080):
        async with static_server:
            await asyncio.Future()


def _build_widget():
    """Rebuild widget bundles if source files are newer than the built outputs."""
    frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')
    src_dir      = os.path.join(frontend_dir, 'src')

    # Find the newest source file mtime
    newest_src = 0.0
    for root, _, files in os.walk(src_dir):
        for f in files:
            mtime = os.path.getmtime(os.path.join(root, f))
            if mtime > newest_src:
                newest_src = mtime

    widgets = [
        ('widget.js',      'build:widget'),
        ('chat-widget.js', 'build:chat-widget'),
    ]

    for out_name, npm_script in widgets:
        out_file    = os.path.join(frontend_dir, 'build', out_name)
        built_mtime = os.path.getmtime(out_file) if os.path.exists(out_file) else 0.0

        if newest_src <= built_mtime:
            logger.info("[widget] %s is up to date — skipping", out_name)
            continue

        logger.info("[widget] Source changed — building %s...", out_name)
        result = subprocess.run(
            f"npm run {npm_script}",
            cwd=frontend_dir,
            capture_output=True,
            text=True,
            shell=True,
        )
        if result.returncode == 0:
            logger.info("[widget] %s built successfully", out_name)
        else:
            logger.warning("[widget] Build failed for %s — serving previous build if available", out_name)
            logger.warning(result.stderr[-500:] if result.stderr else "(no output)")


if __name__ == "__main__":
    _build_widget()
    asyncio.run(main())
