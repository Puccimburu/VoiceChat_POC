"""
Security service — handles:
  1. Domain allowlist  (block requests from unauthorised origins)
  2. Encrypted connection strings  (AES via cryptography.fernet)
"""
import os
import base64
import hashlib
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


