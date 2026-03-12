"""
Security service — handles:
  1. Domain allowlist  (block requests from unauthorised origins)
  2. Encrypted connection strings  (AES via cryptography.fernet)
"""
import os
import base64
import hashlib

# ── Fernet encryption for DB connection strings ──────────────────
try:
    from cryptography.fernet import Fernet
    _ENCRYPT_AVAILABLE = True
except ImportError:
    _ENCRYPT_AVAILABLE = False

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

def check_origin_allowed(origin: str) -> bool:
    """
    Return True if the request origin is in the ALLOWED_ORIGINS env var.
    Empty ALLOWED_ORIGINS = allow all (dev mode).
    """
    from config import ALLOWED_ORIGINS
    if not ALLOWED_ORIGINS or not origin:
        return True

    def normalise(url):
        return url.lower().replace("https://", "").replace("http://", "").rstrip("/")

    o = normalise(origin)
    return any(o == normalise(d) or o.endswith("." + normalise(d)) for d in ALLOWED_ORIGINS)


