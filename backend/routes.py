"""REST API routes — Flask on port 5001."""
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, request as flask_request, jsonify
from flask_cors import CORS
from pymongo import MongoClient, ASCENDING, DESCENDING

from services.qdrant_service         import get_document_list, upload_document
from services.mongodb_agent_service  import MongoDBAgent
from services.sqlite_agent_service   import SQLiteAgent
from services.tts_service            import synthesize_sentence_with_timing
from config                          import API_KEY, DB_TYPE, MONGO_URI, MONGO_DB

logger = logging.getLogger("ws_gateway")

flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "*"}})

# Single shared DB client + config
_mongo_client     = MongoClient(MONGO_URI, maxPoolSize=20, minPoolSize=2)
_db               = _mongo_client[MONGO_DB]
_single_db_config = {"type": DB_TYPE, "connection_string": MONGO_URI, "database": MONGO_DB}


def _get_agent():
    """Fresh agent per request (has per-query state), shared MongoClient."""
    if DB_TYPE == "sqlite":
        return SQLiteAgent(db_config=_single_db_config)
    return MongoDBAgent(db_config=_single_db_config, mongo_client=_mongo_client)


# ── Auth decorator ────────────────────────────────────────────────

def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = (flask_request.headers.get("X-API-Key") or
               flask_request.headers.get("Authorization", "").replace("Bearer ", ""))
        if API_KEY and key != API_KEY:
            return jsonify({"error": "Invalid API key"}), 403
        return f(*args, **kwargs)
    return wrapper


# ── AI agent ──────────────────────────────────────────────────────

@flask_app.route("/api/agent/query", methods=["POST"])
@require_api_key
def agent_query():
    data         = flask_request.json or {}
    query        = data.get("query", "").strip()
    return_audio = data.get("return_audio", False)
    voice        = data.get("voice", "en-US-Neural2-J")
    history      = data.get("history")
    history      = history if isinstance(history, list) else None
    pending      = data.get("pending")
    pending      = pending if isinstance(pending, dict) else None
    if not query:
        return jsonify({"error": "query is required"}), 400
    try:
        agent  = _get_agent()
        text   = agent.query(query, history=history, pending=pending)
        result = {
            "query":     query,
            "response":  text,
            "timestamp": datetime.now().isoformat(),
            "pending":   getattr(agent, "_next_pending", None),
        }
        if return_audio:
            audio_b64, _ = synthesize_sentence_with_timing(text, voice)
            result["audio"] = audio_b64
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Generic CRUD ──────────────────────────────────────────────────

def _build_filter():
    reserved = {"_limit", "_fields", "_sort", "_order"}
    return {k: v for k, v in flask_request.args.items() if k not in reserved}


@flask_app.route("/api/data/<collection>", methods=["GET"])
@require_api_key
def data_list(collection):
    if DB_TYPE == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    try:
        filt   = _build_filter()
        fields = flask_request.args.get("_fields", "")
        proj   = {f.strip(): 1 for f in fields.split(",") if f.strip()} if fields else {}
        proj["_id"] = 0
        limit  = min(int(flask_request.args.get("_limit", 100)), 500)
        sort   = flask_request.args.get("_sort")
        order  = DESCENDING if flask_request.args.get("_order", "asc").lower() == "desc" else ASCENDING
        cursor = _db[collection].find(filt, proj).limit(limit)
        if sort:
            cursor = cursor.sort(sort, order)
        docs = list(cursor)
        return jsonify({collection: docs, "count": len(docs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/api/data/<collection>", methods=["POST"])
@require_api_key
def data_insert(collection):
    if DB_TYPE == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    doc = flask_request.json or {}
    if not doc:
        return jsonify({"error": "Request body required"}), 400
    try:
        doc.setdefault("created_at", datetime.now().isoformat())
        _db[collection].insert_one(doc)
        doc.pop("_id", None)
        return jsonify(doc), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/api/data/<collection>", methods=["PATCH"])
@require_api_key
def data_update(collection):
    if DB_TYPE == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    body   = flask_request.json or {}
    filt   = body.get("filter") or _build_filter()
    update = body.get("update", {})
    if not filt or not update:
        return jsonify({"error": "'filter' and 'update' are required"}), 400
    try:
        res = _db[collection].update_many(filt, {"$set": update})
        return jsonify({"matched": res.matched_count, "modified": res.modified_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/api/data/<collection>", methods=["DELETE"])
@require_api_key
def data_delete(collection):
    if DB_TYPE == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    body = flask_request.json or {}
    filt = body.get("filter") or _build_filter()
    if not filt:
        return jsonify({"error": "filter required — pass in body or as query params"}), 400
    try:
        res = _db[collection].delete_many(filt)
        return jsonify({"deleted": res.deleted_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Document management ───────────────────────────────────────────

@flask_app.route("/admin/documents/upload", methods=["POST"])
@require_api_key
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
@require_api_key
def admin_list_documents():
    try:
        docs = get_document_list()
        return jsonify({"documents": docs, "count": len(docs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Widget / health ───────────────────────────────────────────────

@flask_app.route("/api/widget/init", methods=["GET"])
def widget_init():
    """Return the API key for the widget — single tenant, one key."""
    if API_KEY:
        return jsonify({"api_key": API_KEY})
    return jsonify({"error": "API_KEY not configured"}), 500


@flask_app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "Voice Agent Platform"})


@flask_app.route("/")
def index():
    return jsonify({
        "service": "Voice Agent Platform",
        "endpoints": {
            "POST  /api/agent/query":        "Text -> AI agent response",
            "GET   /api/data/<collection>":  "List/filter documents",
            "POST  /api/data/<collection>":  "Insert document",
            "PATCH /api/data/<collection>":  "Update documents",
            "DELETE /api/data/<collection>": "Delete documents",
            "POST  /admin/documents/upload": "Upload PDF to Qdrant",
            "GET   /admin/documents/list":   "List uploaded documents",
        }
    })
