"""Shared constants used across agent services."""

# Gemini ReAct loop
MAX_LOOP     = 10
_MAX_RETRIES = 4

# Schema / collection caching TTL (seconds)
SCHEMA_TTL      = 300
COLLECTIONS_TTL = 300

# LLM DB answer cache TTL (seconds) — classes/facilities rarely change mid-session
LLM_DB_CACHE_TTL = 3600
