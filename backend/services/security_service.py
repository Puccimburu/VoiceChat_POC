"""
Security service — handles:
  1. Domain allowlist  (block requests from unauthorised origins)
  2. Encrypted connection strings  (AES via cryptography.fernet)
  3. Usage tracking  (session duration, call counts, credit deduction)
"""
import os
import base64
import hashlib
from datetime import datetime
from pymongo import MongoClient

# ── Fernet encryption for DB connection strings ──────────────────
try:
    from cryptography.fernet import Fernet
    _ENCRYPT_AVAILABLE = True
except ImportError:
    _ENCRYPT_AVAILABLE = False

from config import PLATFORM_MONGO_URI, PLATFORM_DB

# Encryption key — store in env in production, never hardcode
# Generate once with: from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())
_RAW_KEY = os.environ.get("ENCRYPTION_KEY", "")

def _get_fernet():
    if not _ENCRYPT_AVAILABLE:
        raise RuntimeError("cryptography package not installed. Run: pip install cryptography")
    if not _RAW_KEY:
        raise RuntimeError("ENCRYPTION_KEY env var not set")
    # Ensure key is 32 url-safe base64 bytes
    key = base64.urlsafe_b64encode(hashlib.sha256(_RAW_KEY.encode()).digest())
    return Fernet(key)


def encrypt_connection_string(plain_uri: str) -> str:
    """Encrypt a MongoDB connection string before storing in platform DB."""
    if not _ENCRYPT_AVAILABLE or not _RAW_KEY:
        # Fallback: store plain (warn in logs)
        print("WARNING: ENCRYPTION_KEY not set — storing connection string unencrypted")
        return plain_uri
    f = _get_fernet()
    return f.encrypt(plain_uri.encode()).decode()


def decrypt_connection_string(encrypted_uri: str) -> str:
    """Decrypt a stored MongoDB connection string before use."""
    if not _ENCRYPT_AVAILABLE or not _RAW_KEY:
        return encrypted_uri   # assume plain
    try:
        f = _get_fernet()
        return f.decrypt(encrypted_uri.encode()).decode()
    except Exception:
        # Not encrypted (e.g. existing plain-text records) — return as-is
        return encrypted_uri


# ── Domain allowlist ─────────────────────────────────────────────

def check_origin_allowed(api_key: str, origin: str) -> bool:
    """
    Return True if the request origin is allowed for this API key.

    If no allowed_domains are set on the key, any origin is allowed
    (useful for development / testing).
    """
    if not origin:
        return True   # no Origin header (e.g. server-to-server) — allow

    try:
        db = MongoClient(PLATFORM_MONGO_URI)[PLATFORM_DB]
        doc = db.api_keys.find_one({"key": api_key}, {"allowed_domains": 1})
        if not doc:
            return False

        allowed = doc.get("allowed_domains", [])
        if not allowed:
            return True   # no restriction configured

        # Normalize: strip protocol and trailing slash
        def normalise(url):
            return url.lower().replace("https://", "").replace("http://", "").rstrip("/")

        origin_clean = normalise(origin)
        return any(normalise(d) in origin_clean for d in allowed)

    except Exception as e:
        print(f"Domain check error: {e}")
        return True   # fail open during dev — flip to False in production


def set_allowed_domains(api_key: str, domains: list[str]):
    """Update the domain allowlist for a given API key."""
    db = MongoClient(PLATFORM_MONGO_URI)[PLATFORM_DB]
    db.api_keys.update_one(
        {"key": api_key},
        {"$set": {"allowed_domains": domains}}
    )


# ── Usage tracking ───────────────────────────────────────────────

def start_session_tracking(api_key: str, session_id: str):
    """Record session start time in usage_sessions collection."""
    try:
        db = MongoClient(PLATFORM_MONGO_URI)[PLATFORM_DB]
        db.usage_sessions.insert_one({
            "api_key":    api_key,
            "session_id": session_id,
            "started_at": datetime.now().isoformat(),
            "ended_at":   None,
            "duration_seconds": None,
            "queries": 0
        })
    except Exception as e:
        print(f"Usage tracking start error: {e}")


def end_session_tracking(session_id: str):
    """Record session end, compute duration, deduct credits."""
    try:
        db = MongoClient(PLATFORM_MONGO_URI)[PLATFORM_DB]
        doc = db.usage_sessions.find_one({"session_id": session_id})
        if not doc or not doc.get("started_at"):
            return

        started   = datetime.fromisoformat(doc["started_at"])
        ended     = datetime.now()
        duration  = round((ended - started).total_seconds())

        db.usage_sessions.update_one(
            {"session_id": session_id},
            {"$set": {"ended_at": ended.isoformat(), "duration_seconds": duration}}
        )

        # Deduct credits (1 credit per 10 seconds of voice)
        credits_used = max(1, duration // 10)
        db.api_keys.update_one(
            {"key": doc["api_key"]},
            {
                "$inc": {
                    "credits_used": credits_used,
                    "total_session_seconds": duration
                }
            }
        )
    except Exception as e:
        print(f"Usage tracking end error: {e}")


def increment_query_count(session_id: str):
    """Increment per-session query counter."""
    try:
        db = MongoClient(PLATFORM_MONGO_URI)[PLATFORM_DB]
        db.usage_sessions.update_one(
            {"session_id": session_id},
            {"$inc": {"queries": 1}}
        )
    except Exception as e:
        print(f"Query count increment error: {e}")


def get_usage_summary(api_key: str) -> dict:
    """Return usage summary for a customer's API key."""
    try:
        db = MongoClient(PLATFORM_MONGO_URI)[PLATFORM_DB]
        key_doc = db.api_keys.find_one({"key": api_key}, {"_id": 0, "key": 0, "db_config": 0})
        sessions = list(db.usage_sessions.find(
            {"api_key": api_key},
            {"_id": 0, "api_key": 0}
        ).sort("started_at", -1).limit(20))

        return {
            "api_calls":             key_doc.get("usage_count", 0),
            "credits_used":          key_doc.get("credits_used", 0),
            "total_session_seconds": key_doc.get("total_session_seconds", 0),
            "recent_sessions":       sessions
        }
    except Exception as e:
        return {"error": str(e)}
