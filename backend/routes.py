"""REST API routes — Flask on port 5001."""
import logging
import os
import secrets
from datetime import datetime
from functools import wraps

from flask import Flask, request as flask_request, jsonify
from flask_cors import CORS
from pymongo import MongoClient, ASCENDING, DESCENDING

from services.session_service        import get_or_create_session  # noqa: F401 (imported for side effects on startup)
from services.qdrant_service         import get_document_list, upload_document
from services.security_service       import encrypt_connection_string, decrypt_connection_string
from services.mongodb_agent_service  import MongoDBAgent
from services.sqlite_agent_service   import SQLiteAgent
from services.tts_service            import synthesize_sentence_with_timing
from config                          import PLATFORM_MONGO_URI, PLATFORM_DB

logger = logging.getLogger("ws_gateway")

ADMIN_SECRET    = os.getenv("ADMIN_SECRET", "")
platform_client = MongoClient(PLATFORM_MONGO_URI)
platform_db     = platform_client[PLATFORM_DB]
_agent_cache: dict = {}

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


# ── Widget / health ───────────────────────────────────────────────

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
