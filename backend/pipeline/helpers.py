import random

_GREETING_WORDS = frozenset({
    "hi", "hello", "hey", "howdy", "sup", "yo",
    "thanks", "thank", "bye", "goodbye", "ok", "okay", "cool",
})

_GREETING_REPLIES = [
    "Hi! How can I help you today?",
    "Hello! What can I do for you?",
    "Hey! How can I help?",
]
_THANKS_REPLIES = [
    "You're welcome! Is there anything else I can help with?",
    "Happy to help! Anything else I can do for you?",
]
_BYE_REPLIES = [
    "Goodbye! Have a great day!",
    "Take care! See you next time.",
    "Bye! Have a wonderful day!",
]


def pick_greeting_reply(transcript: str) -> str:
    words = set(w.strip(".,!? ") for w in transcript.lower().split())
    if words & {"bye", "goodbye"}:
        return random.choice(_BYE_REPLIES)
    if words & {"thanks", "thank"}:
        return random.choice(_THANKS_REPLIES)
    return random.choice(_GREETING_REPLIES)


def is_short_greeting(transcript: str) -> bool:
    words = transcript.lower().split()
    return len(words) <= 4 and any(w.strip(".,!?") in _GREETING_WORDS for w in words)


def pick_filler(transcript: str) -> str:
    words = transcript.lower().split()
    first = words[0].strip(".,!?") if words else ""
    if first in ("what", "who", "which", "where", "when"):
        return random.choice([
            "Let me think about that.", "Good question.",
            "Let me look into that.", "Hmm, let me think.",
        ])
    if first == "how":
        return random.choice([
            "Good question, let me think.", "Let me think through that.",
            "Hmm, let me work through that.",
        ])
    if first == "why":
        return random.choice([
            "Let me think about that.", "Good question, let me think.",
            "Hmm, let me consider that.",
        ])
    if first in ("can", "could", "would", "please"):
        return random.choice([
            "Sure thing.", "Of course.", "Sure, one moment.", "Absolutely.", "Happy to help.",
        ])
    if first in ("explain", "describe", "summarize", "list", "give"):
        return random.choice([
            "Sure, let me explain.", "Let me put that together for you.",
            "Sure, let me break that down.",
        ])
    return random.choice([
        "Let me think about that.", "Sure, one moment.",
        "Hmm, let me think.", "Let me consider that.",
    ])


def extract_sentences(buf: str) -> tuple:
    """Split buf at the first sentence boundary. Returns (sentences, remainder)."""
    for delim in (". ", "! ", "? ", "\n"):
        if delim in buf:
            parts = buf.split(delim)
            return [p + delim for p in parts[:-1]], parts[-1]
    return [], buf
