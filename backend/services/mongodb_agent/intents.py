"""Module-level regex patterns and constants for intent detection."""
import re

# ── Pre-fetch intent detection ─────────────────────────────────────────────
_BOOKINGS_READ_RE = re.compile(
    r"what'?s\s+on\s+my\s+bookings?"
    r"|show\s+(?:me\s+)?my\s+bookings?"
    r"|list\s+(?:my\s+)?bookings?"
    r"|what\s+(?:are\s+)?my\s+bookings?"
    r"|how\s+many\s+bookings?"
    r"|which\s+bookings?\s+(?:do\s+i|have\s+i)"
    r"|do\s+i\s+have\s+(?:any\s+|a\s+)?bookings?"
    r"|my\s+(?:upcoming\s+|current\s+|existing\s+)?bookings?"
    r"|what\s+have\s+i\s+booked"
    r"|what'?s\s+booked\s+for\s+me"
    r"|bookings?\s+(?:i\s+have|i\s+(?:currently\s+)?have\s+now)"
    r"|bookings?\s+(?:available\s+)?for\s+me"
    r"|(?:what|which)\s+bookings?\s+(?:are\s+)?(?:there\s+)?for\s+me"
    r"|(?:the\s+)?bookings?\s+i\s+have(?:\s+now|\s+currently)?",
    re.IGNORECASE,
)
_BOOKING_WRITE_RE = re.compile(
    r"\b(?:book\s+me|book\s+for\s+me|enroll\s+me|enrol\s+me|sign\s+me|join|"
    r"reserve\s+me|get\s+me\s+into|add\s+me\s+to|put\s+me\s+in|"
    r"i\s+want\s+to\s+book|i\s+would\s+like\s+to\s+book|i'd\s+like\s+to\s+book|"
    r"can\s+you\s+book)\b",
    re.IGNORECASE,
)
_CLASSES_READ_RE = re.compile(
    r"what\s+classes?\s+(?:are|do\s+you\s+have|are\s+available|is\s+available|are\s+on)"
    r"|available\s+classes?"
    r"|show\s+(?:me\s+)?(?:the\s+)?classes?"
    r"|list\s+(?:all\s+)?classes?"
    r"|what\s+are\s+(?:the|your)\s+classes?",
    re.IGNORECASE,
)
_MEMBER_NAME_RE = re.compile(r'\[CURRENT USER:.*?name=([^,\]]+)', re.IGNORECASE)

# Write-intent detector — queries matching this go to the ReAct loop only
_WRITE_INTENT_RE = re.compile(
    r'\b(?:book\s+me|book\s+for\s+me|enroll\s+me|enrol\s+me|sign\s+me|'
    r'reserve\s+me|get\s+me\s+into|add\s+me\s+to|put\s+me\s+in|'
    r'i\s+want\s+to\s+book|i\s+would\s+like\s+to\s+book|i\'?d\s+like\s+to\s+book|'
    r'can\s+you\s+book|could\s+you\s+book|would\s+you\s+book|please\s+book|'
    r'cancel|reschedule|change\s+my\s+booking|'
    r'move\s+my\s+booking|remove\s+my\s+booking|delete\s+my\s+booking)\b',
    re.IGNORECASE,
)

# Topic detectors for LLM DB collection routing
_CLASSES_TOPIC_RE    = re.compile(r'\bclass(?:es)?\b|\binstructor\b|\bsession\b|\bspots?\b', re.IGNORECASE)
_FACILITIES_TOPIC_RE = re.compile(r'\bfacilit|\bcourt\b|\bpool\b|\bgym\b|\broom\b|\bhall\b|\bstudio\b', re.IGNORECASE)
# Cost queries must go to LLM DB (has amount field) — skip Direct DB which can't calculate totals
_COST_QUERY_RE = re.compile(r'\b(?:cost|price|fee|amount|total|how\s+much)\b', re.IGNORECASE)
# Softer "my bookings" topic — catches phrasings not covered by _BOOKINGS_READ_RE Direct-DB path
_MY_BOOKINGS_TOPIC_RE = re.compile(
    r'\bmy\s+(?:upcoming\s+)?(?:bookings?|sessions?|appointments?|reservations?|schedule)\b'
    r'|\bbookings?\s+i\s+(?:have|made|got)\b'
    r'|\bwhat\s+(?:have\s+i|am\s+i)\s+(?:booked|bookings?)\b'
    r'|\bwhat\s+(?:do\s+i\s+have|is\s+(?:on\s+)?my\s+schedule)\b'
    r'|\b(?:cost|price|fee|amount|total|how\s+much).*\bmy\s+bookings?\b'
    r'|\bmy\s+bookings?.*\b(?:cost|price|fee|amount|total)\b'
    r'|\bmy\b.*\b(?:cost|price|fee|amount|total)\b.*\bbookings?\b'
    r'|\bmy\b.*\bbookings?\b.*\b(?:cost|price|fee|amount|total)\b'
    r'|\b(?:all|five|the)\s+bookings?\b'
    r'|\b(?:have\s+you|did\s+you)\s+book(?:ed)?\b'
    r'|\byou\s+(?:just\s+)?book(?:ed)?\b'
    r'|\bwas\s+(?:it|that)\s+book(?:ed)?\b',
    re.IGNORECASE,
)
