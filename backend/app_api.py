"""
Voice Agent Platform API — port 5001
Multi-tenant REST API: AI voice agent + generic CRUD for any customer database.

Customer endpoints  (X-API-Key: va_... required):
  POST  /api/agent/query             — Text → AI agent response (+ optional audio)
  GET   /api/data/<collection>       — List / filter documents
  POST  /api/data/<collection>       — Insert a document
  PATCH /api/data/<collection>       — Update documents  { filter, update } in body
  DELETE /api/data/<collection>      — Delete documents  { filter } in body or params

Admin endpoints  (X-Admin-Token required):
  POST /admin/customers/register     — Register customer + generate API key
  GET  /admin/customers              — List all customers and keys
  POST /admin/customers/<id>/revoke  — Deactivate a customer's key
  POST /admin/documents/upload       — Upload PDF to knowledge base (Qdrant)
  GET  /admin/documents/list         — List indexed PDFs
"""
import os, json, secrets
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING

load_dotenv()
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

from services.mongodb_agent_service import MongoDBAgent
from services.sqlite_agent_service  import SQLiteAgent
from services.tts_service           import synthesize_sentence_with_timing
from services.qdrant_service        import get_document_list, upload_document
from services.security_service      import encrypt_connection_string, decrypt_connection_string
from config import PLATFORM_MONGO_URI, PLATFORM_DB

platform_client = MongoClient(PLATFORM_MONGO_URI)
platform_db     = platform_client[PLATFORM_DB]
_agent_cache: dict = {}

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})


# ── Auth ──────────────────────────────────────────────────────────────────────

def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").replace("Bearer ", "")
        if not key:
            return jsonify({"error": "API key required"}), 401
        doc = platform_db.api_keys.find_one({"key": key, "active": True})
        if not doc:
            return jsonify({"error": "Invalid or inactive API key"}), 403
        platform_db.api_keys.update_one({"key": key}, {"$inc": {"usage_count": 1}, "$set": {"last_used": datetime.now().isoformat()}})
        request.customer_id   = doc["customer_id"]
        request.customer_name = doc["customer_name"]
        request.db_config     = doc.get("db_config", {})
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ADMIN_SECRET:
            return f(*args, **kwargs)
        token = request.headers.get("X-Admin-Token") or request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != ADMIN_SECRET:
            return jsonify({"error": "Admin token required or invalid"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Helpers ───────────────────────────────────────────────────────────────────

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
    """Return a pymongo Database for the currently authenticated customer."""
    cfg = request.db_config
    return MongoClient(decrypt_connection_string(cfg.get("connection_string", "")))[cfg.get("database", "")]


def _build_filter():
    """
    Build a MongoDB filter from query params, skipping reserved keys.
    Reserved: _limit, _fields, _sort, _order
    Example: GET /api/data/bookings?member_id=M001&status=confirmed
    """
    reserved = {"_limit", "_fields", "_sort", "_order"}
    return {k: v for k, v in request.args.items() if k not in reserved}


# ── Customer: AI agent ────────────────────────────────────────────────────────

@app.route("/api/agent/query", methods=["POST"])
@require_api_key
def agent_query():
    data         = request.json or {}
    query        = data.get("query", "").strip()
    return_audio = data.get("return_audio", False)
    voice        = data.get("voice", "en-US-Neural2-J")
    history      = data.get("history")
    pending      = data.get("pending")
    if not query:
        return jsonify({"error": "query is required"}), 400
    try:
        agent  = _get_agent(request.customer_id, request.db_config)
        text   = agent.query(query, history=history, pending=pending)
        result = {
            "query":     query,
            "response":  text,
            "customer":  request.customer_name,
            "timestamp": datetime.now().isoformat(),
            "pending":   getattr(agent, "_next_pending", None),
        }
        if return_audio:
            audio_b64, _ = synthesize_sentence_with_timing(text, voice)
            result["audio"] = audio_b64
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Customer: generic CRUD ────────────────────────────────────────────────────
# Works for any MongoDB customer. SQLite customers use the voice agent for queries.
#
# GET params:
#   _fields=f1,f2   projection (default: all, _id hidden)
#   _limit=N        max results (default 100, max 500)
#   _sort=field     sort field
#   _order=asc|desc sort direction (default asc)
#   anything else   added to MongoDB filter  e.g. ?status=confirmed&member_id=M001
#
# PATCH/DELETE: pass { "filter": {...}, "update": {...} } in JSON body,
#               OR use query params as the filter (same as GET).

@app.route("/api/data/<collection>", methods=["GET"])
@require_api_key
def data_list(collection):
    if request.db_config.get("type") == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    try:
        db     = _customer_db()
        filt   = _build_filter()
        fields = request.args.get("_fields", "")
        proj   = {f.strip(): 1 for f in fields.split(",") if f.strip()} if fields else {}
        proj["_id"] = 0
        limit  = min(int(request.args.get("_limit", 100)), 500)
        sort   = request.args.get("_sort")
        order  = DESCENDING if request.args.get("_order", "asc").lower() == "desc" else ASCENDING
        cursor = db[collection].find(filt, proj).limit(limit)
        if sort:
            cursor = cursor.sort(sort, order)
        docs = list(cursor)
        return jsonify({collection: docs, "count": len(docs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/data/<collection>", methods=["POST"])
@require_api_key
def data_insert(collection):
    if request.db_config.get("type") == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    doc = request.json or {}
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


@app.route("/api/data/<collection>", methods=["PATCH"])
@require_api_key
def data_update(collection):
    if request.db_config.get("type") == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    body   = request.json or {}
    filt   = body.get("filter") or _build_filter()
    update = body.get("update", {})
    if not filt or not update:
        return jsonify({"error": "'filter' and 'update' are required"}), 400
    try:
        res = _customer_db()[collection].update_many(filt, {"$set": update})
        return jsonify({"matched": res.matched_count, "modified": res.modified_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/data/<collection>", methods=["DELETE"])
@require_api_key
def data_delete(collection):
    if request.db_config.get("type") == "sqlite":
        return jsonify({"error": "Use /api/agent/query for SQLite databases"}), 400
    body = request.json or {}
    filt = body.get("filter") or _build_filter()
    if not filt:
        return jsonify({"error": "filter required — pass in body or as query params"}), 400
    try:
        res = _customer_db()[collection].delete_many(filt)
        return jsonify({"deleted": res.deleted_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Admin: customer management ────────────────────────────────────────────────

@app.route("/admin/customers/register", methods=["POST"])
@require_admin
def register_customer():
    data      = request.json or {}
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
    return jsonify({"message": "Customer registered successfully", "customer_id": customer_id, "customer_name": name, "api_key": api_key}), 201


@app.route("/admin/customers", methods=["GET"])
@require_admin
def list_customers():
    return jsonify({"customers": list(platform_db.customers.find({}, {"_id": 0})), "api_keys": list(platform_db.api_keys.find({}, {"_id": 0}))})


@app.route("/admin/customers/<customer_id>/revoke", methods=["POST"])
@require_admin
def revoke_key(customer_id):
    platform_db.api_keys.update_many({"customer_id": customer_id}, {"$set": {"active": False}})
    return jsonify({"message": f"API key(s) for {customer_id} revoked"})


# ── Admin: document management ────────────────────────────────────────────────

@app.route("/admin/documents/upload", methods=["POST"])
@require_admin
def admin_upload_document():
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"success": False, "message": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"success": False, "message": "Only PDF files are supported"}), 400
    try:
        result = upload_document(file.read(), file.filename)
        return jsonify(result), 200 if result["success"] else 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/admin/documents/list", methods=["GET"])
@require_admin
def admin_list_documents():
    try:
        docs = get_document_list()
        return jsonify({"documents": docs, "count": len(docs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Public ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "Voice Agent Platform API"})


@app.route("/")
def index():
    return jsonify({
        "service": "Voice Agent Platform API",
        "customer_endpoints": {
            "POST  /api/agent/query":        "Text → AI agent response",
            "GET   /api/data/<collection>":  "List/filter — params: _fields _limit _sort _order + any field=value filter",
            "POST  /api/data/<collection>":  "Insert document",
            "PATCH /api/data/<collection>":  'Update — body: {"filter":{...}, "update":{...}}',
            "DELETE /api/data/<collection>": 'Delete — body: {"filter":{...}}  or query params',
        },
        "admin_endpoints": {
            "POST /admin/customers/register":    "Register customer + API key",
            "GET  /admin/customers":             "List all customers",
            "POST /admin/customers/<id>/revoke": "Revoke API key",
            "POST /admin/documents/upload":      "Upload PDF",
            "GET  /admin/documents/list":        "List documents",
        }
    })


if __name__ == "__main__":
    print("=" * 60)
    print("Voice Agent Platform API  —  port 5001")
    print("=" * 60)
    app.run(debug=True, port=5001)
