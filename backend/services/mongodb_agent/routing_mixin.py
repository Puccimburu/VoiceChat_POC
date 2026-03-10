"""AgentRoutingMixin — direct-answer, LLM-DB, and prefetch paths."""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from google.genai import types

from pipeline.helpers import extract_sentences as _extract_sentences
from services.gemini_client import (
    gemini_call as _gemini_call,
    gemini_stream_content as _gemini_stream_content,
)
from .intents import (
    _BOOKING_WRITE_RE,
    _BOOKINGS_READ_RE,
    _CLASSES_READ_RE,
    _CLASSES_TOPIC_RE,
    _FACILITIES_TOPIC_RE,
    _MEMBER_NAME_RE,
    _MY_BOOKINGS_TOPIC_RE,
    _WRITE_INTENT_RE,
)
from .cache import _llm_db_cache_get, _llm_db_cache_set

logger = logging.getLogger("ws_gateway")

_BLOCKED = {"admin", "api_keys", "customers"}


class AgentRoutingMixin:
    """Mixin providing direct-answer, LLM-DB, and prefetch routing methods."""

    # ── Static formatters ────────────────────────────────────────────────

    @staticmethod
    def _format_bookings(results: list, member_name: str) -> str:
        if not results:
            return "You don't have any upcoming bookings."
        parts = []
        for bk in results:
            subject = bk.get("class_name") or ""
            if not subject:
                fac = bk.get("facility_name", "booking")
                subject = fac[7:] if fac.startswith("Class: ") else fac
            date_str  = bk.get("booking_date", "")
            time_slot = bk.get("time_slot", "")
            display_date = date_str
            if date_str:
                try:
                    dt = datetime.fromisoformat(str(date_str))
                    display_date = f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}"
                except (ValueError, TypeError):
                    pass
            entry = subject
            if display_date:
                entry += f" on {display_date}"
            if time_slot:
                time_part = time_slot.split("·")[-1].strip() if "·" in time_slot else time_slot
                entry += f" at {time_part}"
            parts.append(entry)
        count = len(parts)
        if count == 1:
            return f"You have one upcoming booking: {parts[0]}."
        joined = ", ".join(parts[:-1]) + f", and {parts[-1]}"
        return f"You have {count} upcoming bookings: {joined}."

    @staticmethod
    def _format_classes(results: list) -> str:
        if not results:
            return "There are no classes available at the moment."
        sentences = []
        for cls in results:
            name       = cls.get("name", "class")
            instructor = cls.get("instructor", "")
            schedule   = cls.get("schedule", {})
            days       = schedule.get("days", [])
            time_str   = schedule.get("time", "")
            capacity   = cls.get("capacity", 0)
            enrolled   = cls.get("enrolled", 0)
            spots_left = max(0, capacity - enrolled)
            line = name
            if instructor:
                line += f" with {instructor}"
            if days:
                line += f" on {', '.join(days)}"
            if time_str:
                line += f" at {time_str}"
            if capacity:
                word = "spot" if spots_left == 1 else "spots"
                line += f", {spots_left} {word} left"
            sentences.append(line + ".")
        count = len(sentences)
        if count == 1:
            return f"We have one class available: {sentences[0]}"
        return f"We have {count} classes available. " + " ".join(sentences)

    # ── Direct-answer path ───────────────────────────────────────────────

    def _try_direct_answer(self, user_query: str, today: str) -> str:
        """Return a fully-formatted spoken response for simple read intents,
        bypassing the LLM entirely. Returns '' to fall through to LLM."""
        speech = user_query
        if speech.startswith("[CURRENT USER:"):
            idx = speech.find("]")
            if idx != -1:
                speech = speech[idx + 1:].strip()

        # Write intents always go to LLM
        if _BOOKING_WRITE_RE.search(speech):
            return ""

        m = _MEMBER_NAME_RE.search(user_query)
        member_name = m.group(1).strip() if m else None

        if _BOOKINGS_READ_RE.search(speech) and member_name:
            data = self._do_query({
                "collection": "bookings",
                "filter": {"member_name": member_name, "booking_date": {"$gte": today}},
            })
            logger.info(f"[Agent] direct-answer: bookings for {member_name} ({data.get('count', 0)} results)")
            return self._format_bookings(data.get("results", []), member_name)

        if _CLASSES_READ_RE.search(speech):
            data = self._do_query({"collection": "classes", "filter": {}, "limit": 20})
            logger.info(f"[Agent] direct-answer: classes ({data.get('count', 0)} results)")
            return self._format_classes(data.get("results", []))

        return ""

    # ── LLM-DB path ──────────────────────────────────────────────────────

    # Pronouns that signal the user is referring to something from the previous turn
    _PRONOUN_RE = re.compile(r'\b(?:they|them|those|it|these|the\s+ones?)\b', re.IGNORECASE)

    def _try_llm_db_answer(self, speech: str, member_name: str = None,
                           on_sentence=None, _start_num: int = 1) -> str:
        """Fetch relevant MongoDB data and answer with a single Gemini call (no ReAct loop).
        Returns '' to fall through to the ReAct loop if no data found or on error."""
        # Route to relevant collections based on query topic
        to_fetch = []
        booking_filter = None
        is_personal_booking_query = False
        if _MY_BOOKINGS_TOPIC_RE.search(speech) and member_name and "bookings" in self.collections:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            booking_filter = {"member_name": member_name, "booking_date": {"$gte": today}}
            to_fetch.append("bookings")
            is_personal_booking_query = True
        if _CLASSES_TOPIC_RE.search(speech):
            to_fetch.append("classes")
        if _FACILITIES_TOPIC_RE.search(speech):
            if "facilities" in self.collections:
                to_fetch.append("facilities")
        if not to_fetch:
            # Generic read — fetch all info collections (skip bookings/members for privacy)
            skip = _BLOCKED | {"bookings", "members"}
            to_fetch = [c for c in self.collections if c not in skip]

        # Redis cache — skip for personal booking queries (member-specific, must be fresh)
        db_key = self._cache_key
        if not is_personal_booking_query:
            cached = _llm_db_cache_get(db_key, speech)
            if cached:
                logger.info(f"[Agent] LLM-DB cache hit for: {speech[:60]!r}")
                return cached

        # Fetch all collections in parallel
        def _fetch(col):
            filt = booking_filter if (col == "bookings" and booking_filter) else {}
            return col, self._do_query({"collection": col, "filter": filt, "limit": 20})

        cols_to_fetch = [c for c in to_fetch if c in self.collections]
        context_parts = []

        def _build_context_part(col, results):
            if not results:
                return
            clean = [
                {k: v for k, v in r.items() if k not in ("_id", "created_at", "source", "status")}
                for r in results
            ]
            part = f"Collection '{col}':\n{json.dumps(clean, indent=2)}"
            # Pre-compute total for personal booking queries so Gemini doesn't have to add
            if col == "bookings" and is_personal_booking_query:
                total = sum(r.get("amount", 0) for r in results if isinstance(r.get("amount"), (int, float)))
                if total > 0:
                    part += f"\nPRE-COMPUTED TOTAL COST: {total}"
            context_parts.append(part)

        if len(cols_to_fetch) > 1:
            with ThreadPoolExecutor(max_workers=len(cols_to_fetch)) as pool:
                futures = {pool.submit(_fetch, col): col for col in cols_to_fetch}
                results_map = {}
                for fut in as_completed(futures):
                    col, data = fut.result()
                    results_map[col] = data
            for col in cols_to_fetch:
                _build_context_part(col, results_map[col].get("results", []))
        else:
            for col in cols_to_fetch:
                data = self._do_query({"collection": col, "filter": booking_filter or {}, "limit": 20})
                _build_context_part(col, data.get("results", []))

        if not context_parts:
            return ""

        db_context = "\n\n".join(context_parts)
        user_line = f"CURRENT USER: {member_name}\n" if member_name else ""
        prompt = (
            f"You are a voice assistant for {self.schema_desc or 'a business'}. "
            f"Answer the question using ONLY the data below. "
            f"Be natural and conversational — no markdown, no bullet points, keep it concise.\n\n"
            f"{user_line}"
            f"DATA:\n{db_context}\n\n"
            f"QUESTION: {speech}"
        )
        try:
            cfg = types.GenerateContentConfig(temperature=0.1)
            if on_sentence is not None:

                _buf = [""]
                _num = [_start_num]
                _all = [""]

                def _on_chunk(chunk):
                    _buf[0] += chunk
                    _all[0] += chunk
                    while True:
                        sentences, remaining = _extract_sentences(_buf[0])
                        if not sentences:
                            _buf[0] = remaining
                            break
                        for s in sentences:
                            if s.strip():
                                on_sentence(s, _num[0])
                                _num[0] += 1
                        _buf[0] = remaining

                _gemini_stream_content(self.gemini, prompt, cfg, _on_chunk)
                if _buf[0].strip():
                    on_sentence(_buf[0].strip(), _num[0])
                text = _all[0].strip()
            else:
                response = _gemini_call(self.gemini, prompt, cfg)
                parts = (response.candidates[0].content.parts or []) if response.candidates else []
                text = "".join(p.text for p in parts if hasattr(p, "text") and p.text).strip()

            logger.info(f"[Agent] LLM-DB answer for: {speech[:60]!r}")
            if text and not is_personal_booking_query:
                _llm_db_cache_set(db_key, speech, text)
            return text
        except Exception as e:
            logger.error(f"[Agent] LLM-DB error: {e}")
            return ""

    # ── Prefetch write data ──────────────────────────────────────────────

    def _prefetch_write_data(self, speech: str, user_query: str, today: str) -> str:
        """Pre-fetch DB data for write intents so Gemini can skip query_collection on turn 1.
        Returns the prefetch context string (empty if not a write intent)."""
        if not _WRITE_INTENT_RE.search(speech):
            return ""
        _prefetch_parts = []
        _m_pre = _MEMBER_NAME_RE.search(user_query)
        _mn_pre = _m_pre.group(1).strip() if _m_pre else None
        _is_cancel_reschedule = bool(re.search(
            r'\bcancel\b|\breschedule\b|\bmove\s+my\b|\bchange\s+my\b', speech, re.IGNORECASE
        ))
        if _is_cancel_reschedule and _mn_pre and "bookings" in self.collections:
            data = self._do_query({"collection": "bookings",
                                   "filter": {"member_name": _mn_pre, "booking_date": {"$gte": today}},
                                   "limit": 20})
            if data.get("results"):
                _prefetch_parts.append(
                    f"Collection 'bookings':\n{json.dumps(data['results'], indent=2, default=str)}"
                )
        else:
            if "classes" in self.collections:
                data = self._do_query({"collection": "classes", "filter": {}, "limit": 20})
                if data.get("results"):
                    clean = [{k: v for k, v in r.items() if k not in ("_id", "created_at", "source")}
                             for r in data["results"]]
                    _prefetch_parts.append(f"Collection 'classes':\n{json.dumps(clean, indent=2)}")
            if "facilities" in self.collections:
                data = self._do_query({"collection": "facilities", "filter": {}, "limit": 20})
                if data.get("results"):
                    clean = [{k: v for k, v in r.items() if k not in ("_id", "created_at", "source")}
                             for r in data["results"]]
                    _prefetch_parts.append(f"Collection 'facilities':\n{json.dumps(clean, indent=2)}")
        if _prefetch_parts:
            _fetched_names = [p.split("'")[1] for p in _prefetch_parts if "'" in p]
            logger.info(f"[Agent] prefetched {_fetched_names}")
            return (
                "\n\nPRE-FETCHED DATA (already loaded — MANDATORY: do NOT call query_collection "
                "for any collection listed below; use this data directly to proceed):\n"
                + "\n\n".join(_prefetch_parts)
            )
        return ""
