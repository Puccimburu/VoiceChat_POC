"""MongoDBAgent — main class combining all mixins."""
import logging
import re
import time

from pymongo import MongoClient
from google import genai
from google.genai import types

from constants import SCHEMA_TTL, COLLECTIONS_TTL
from config import GEMINI_API_KEY, PLATFORM_MONGO_URI, PLATFORM_DB
from pipeline.helpers import extract_sentences as _extract_sentences
from services.gemini_client import gemini_call as _gemini_call

from .routing_mixin import AgentRoutingMixin
from .crud_mixin import AgentCrudMixin
from .tools_mixin import AgentToolsMixin
from .prompt_mixin import AgentPromptMixin
from .descriptions import build_class_description, build_booking_description
from .intents import _WRITE_INTENT_RE, _COST_QUERY_RE, _MEMBER_NAME_RE

logger = logging.getLogger("ws_gateway")

# ── Module-level shared state ────────────────────────────────────────────────
_gemini            = genai.Client(api_key=GEMINI_API_KEY)
_BLOCKED           = {"admin", "api_keys", "customers"}
_SCHEMA_TTL        = SCHEMA_TTL
_COLLECTIONS_TTL   = COLLECTIONS_TTL
_schema_store:      dict = {}
_collections_cache: dict = {}


class MongoDBAgent(AgentRoutingMixin, AgentCrudMixin, AgentToolsMixin, AgentPromptMixin):

    def __init__(self, db_config: dict = None, mongo_client: MongoClient = None):
        self.gemini       = _gemini
        cfg               = db_config or {}
        conn_str          = cfg.get("connection_string", PLATFORM_MONGO_URI)
        db_name           = cfg.get("database", PLATFORM_DB)
        self.schema_desc  = cfg.get("schema_description", "")
        cols              = cfg.get("collections", [])
        # Accept a pre-built client so callers can share connection pools across tenants
        self.mongo_client = mongo_client or MongoClient(conn_str)
        self.db           = self.mongo_client[db_name]
        self._cache_key   = (conn_str, db_name)
        if not cols:
            entry = _collections_cache.get(self._cache_key)
            if entry and (time.time() - entry["ts"]) < _COLLECTIONS_TTL:
                cols = entry["cols"]
            else:
                cols = [c for c in self.db.list_collection_names() if c not in _BLOCKED]
                _collections_cache[self._cache_key] = {"cols": cols, "ts": time.time()}
        self.collections  = cols
        self._tools                    = self._build_tools()
        self._next_pending             = None
        self._last_queried_for_booking = None   # tracks most-recent single-result facility/class lookup
        self._last_class_result        = None   # full doc for fallback spoken response if model goes empty
        self._last_booking_result      = None   # full doc for fallback when model goes empty after bookings query

    # ── Schema ───────────────────────────────────────────────────────────

    def _schema(self) -> str:
        entry = _schema_store.get(self._cache_key)
        if entry and (time.time() - entry["ts"]) < _SCHEMA_TTL:
            return entry["schema"]
        parts = []
        for col in self.collections:
            samples = list(self.db[col].find({}, {"_id": 0}).limit(3))
            if not samples:
                parts.append(f"  - {col}: (empty)")
                continue
            fields    = list(samples[0].keys())
            name_keys = [k for k in fields if "name" in k.lower() or k == "title"]
            vals      = [str(v) for k in name_keys for v in self.db[col].distinct(k) if v]
            parts.append(f"  - {col}: fields={fields}" + (f", values={vals}" if vals else ""))
        base   = "Collections:\n" + "\n".join(parts)
        schema = f"{self.schema_desc}\n\n{base}" if self.schema_desc else base
        _schema_store[self._cache_key] = {"schema": schema, "ts": time.time()}
        return schema

    def _invalidate_schema(self):
        _schema_store.pop(self._cache_key, None)
        _collections_cache.pop(self._cache_key, None)

    # ── Class-level constants ─────────────────────────────────────────────

    _DATE_TIME_WORDS = frozenset(["date", "time", "when", "slot", "day", "morning", "afternoon", "evening"])

    # Simple yes-phrases that mean "go ahead" with no caveats or new info
    _YES_PHRASES = frozenset({
        "yes", "yes please", "yes please.", "yes,", "yes.", "yep", "yeah",
        "sure", "sure.", "ok", "ok.", "okay", "okay.", "alright", "alright.",
        "go ahead", "go ahead.", "do it", "do it.", "proceed", "proceed.",
        "confirm", "confirm.", "correct", "correct.", "please", "please.",
        "absolutely", "perfect", "great", "fine", "sounds good", "that's right",
        "thats right", "that is correct", "that's correct",
    })

    @staticmethod
    def _is_simple_yes(text: str) -> bool:
        """True when the user's message is a plain confirmation with no new info."""
        t = text.strip().lower().rstrip(".,!?")
        if t in MongoDBAgent._YES_PHRASES:
            return True
        # Strip all internal punctuation so "yes. proceed" / "yes, go ahead" → "yes proceed"
        t_clean = re.sub(r'[^\w\s]', '', t).strip()
        if t_clean in MongoDBAgent._YES_PHRASES:
            return True
        # "yes please", "yes go ahead", "yes do it", "yes proceed" — short yes-prefixed phrases
        words = t_clean.split()
        return bool(words) and words[0] == "yes" and len(words) <= 3

    # ── Entry point ──────────────────────────────────────────────────────

    def query(self, user_query: str, history: list = None, pending: dict = None,
              on_sentence=None, _start_num: int = 1) -> str:
        self._next_pending             = None
        self._last_queried_for_booking = None
        self._last_class_result        = None
        self._last_booking_result      = None
        # Guards that prevent write tools from firing without a prior confirm_action
        self._confirm_action_called  = False
        self._incoming_awaiting      = bool(pending and pending.get("awaiting_confirmation"))
        self._incoming_action_type   = (pending.get("action_type", "") if self._incoming_awaiting else "")

        # Strip any [CURRENT USER: ...] prefix that agent.py prepends.
        _speech_only = user_query
        if _speech_only.startswith("[CURRENT USER:"):
            _bracket_end = _speech_only.find("]")
            if _bracket_end != -1:
                _speech_only = _speech_only[_bracket_end + 1:].strip()

        # ── Fast-path: bypass Gemini for simple confirmations ──────────────
        from datetime import datetime
        fast = self._handle_fast_confirmation(_speech_only, pending or {})
        if fast is not None:
            return fast

        now   = datetime.now()
        today = now.strftime("%Y-%m-%d")

        # Fast-path: handle simple read intents directly — no LLM call at all
        # Skip if write intent or cost query present (cost queries need LLM DB to calculate totals)
        if not pending and not _WRITE_INTENT_RE.search(_speech_only) and not _COST_QUERY_RE.search(_speech_only):
            direct = self._try_direct_answer(user_query, today)
            if direct:
                logger.info("[Agent] direct-answer fast-path — skipping LLM")
                return direct

        # LLM DB path: intelligent reads — 1 Gemini call, no tool loop
        # Only for read queries with no pending state and no write intent
        if not pending and not _WRITE_INTENT_RE.search(_speech_only):
            _m = _MEMBER_NAME_RE.search(user_query)
            _member_name_for_llm = _m.group(1).strip() if _m else None
            # Pronoun resolution: if query has "they/them/it" and last response mentioned bookings,
            # rewrite the query in Python before routing — zero LLM token overhead
            _llm_speech = _speech_only
            if MongoDBAgent._PRONOUN_RE.search(_speech_only):
                _last_assistant = (history[-1].get("assistant", "") if history else "")
                if "booking" in _last_assistant.lower():
                    _llm_speech = MongoDBAgent._PRONOUN_RE.sub("my bookings", _speech_only)
            llm_db = self._try_llm_db_answer(_llm_speech, member_name=_member_name_for_llm,
                                             on_sentence=on_sentence, _start_num=_start_num)
            if llm_db:
                logger.info("[Agent] LLM-DB path — 1 Gemini call, no ReAct loop")
                return llm_db

        date_ref          = self._build_date_context(now)
        _prefetch_context = self._prefetch_write_data(_speech_only, user_query, today)

        system = self._build_system_prompt(today, date_ref)
        if _prefetch_context:
            system += _prefetch_context

        contents = []
        if history:
            for turn in history[-4:]:
                contents.append(types.Content(role="user",  parts=[types.Part(text=turn["user"])]))
                contents.append(types.Content(role="model", parts=[types.Part(text=turn["assistant"])]))

        user_text = self._build_user_message(user_query, pending)
        contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[self._tools],
            temperature=0.1,
        )

        return self._run_react_loop(contents, config, pending, user_text, on_sentence, _start_num)

    def _run_react_loop(self, contents, config, pending, user_text, on_sentence, _start_num):
        """Execute the Gemini ReAct loop: call → dispatch tools → repeat until text response."""
        from constants import MAX_LOOP
        _react_sent_num = [_start_num]

        for i in range(MAX_LOOP):
            print(f"[Agent] Loop {i + 1}")
            response = _gemini_call(self.gemini, contents, config)
            model_content = response.candidates[0].content

            contents.append(model_content)

            parts = model_content.parts or [] if model_content else []
            function_calls = [
                p.function_call for p in parts
                if hasattr(p, "function_call") and p.function_call
            ]

            if not function_calls:
                text = "".join(
                    p.text for p in parts
                    if hasattr(p, "text") and p.text
                ).strip()

                if not text:
                    print(f"[Agent] WARNING: model returned empty (no tools, no text). Loop {i+1}. Query was: {user_text[:300]}")
                    # Fallback: if the model went empty after a single-class query,
                    # synthesize the class description in Python so the user still
                    # hears the class info and a day-selection prompt.
                    if self._last_class_result:
                        text = build_class_description(self._last_class_result)
                        print(f"[Agent] Synthesized class fallback for: {self._last_class_result.get('name')}")
                        if not self._next_pending and self._last_queried_for_booking:
                            self._next_pending = self._last_queried_for_booking
                    # Fallback: if the model went empty after a single-booking query
                    # (e.g. user said "Yes." with no active pending state), synthesize
                    # a spoken summary of the booking and ask what to do with it.
                    elif self._last_booking_result:
                        text = build_booking_description(self._last_booking_result)
                        print(f"[Agent] Synthesized booking fallback for: {self._last_booking_result.get('member_name')} / {self._last_booking_result.get('class_name') or self._last_booking_result.get('facility_name')}")

                # Auto-preserve booking context: if the model returned plain text asking
                # for date/time (instead of using ask_user tool), save the last queried
                # facility/class so the next turn still has partial booking context.
                if (not self._next_pending
                        and self._last_queried_for_booking
                        and text
                        and any(w in text.lower() for w in self._DATE_TIME_WORDS)):
                    self._next_pending = self._last_queried_for_booking

                final = text or "Sorry, I didn't quite catch that. Could you say that again?"
                if on_sentence is not None:
                    buf = final
                    while True:
                        sentences, buf = _extract_sentences(buf)
                        if not sentences:
                            break
                        for s in sentences:
                            if s.strip():
                                on_sentence(s, _react_sent_num[0])
                                _react_sent_num[0] += 1
                    if buf.strip():
                        on_sentence(buf.strip(), _react_sent_num[0])
                return final

            tool_response_parts = []
            early_return        = None

            for fc in function_calls:
                args   = dict(fc.args) if fc.args else {}
                result = self._run_tool(fc.name, args)
                print(f"[Agent] {fc.name}({args}) -> {result}")

                if "__confirm__" in result:
                    # Save full action details for next turn
                    self._next_pending = {
                        "awaiting_confirmation":   True,
                        "confirmation_summary":    result["__confirm__"],
                        "action_type":             result["action_type"],
                        "collection":              result["collection"],
                        "document":                result["document"],
                        "filter":                  result["filter"],
                        "updates":                 result["updates"],
                    }
                    early_return = result["__confirm__"]
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": "Confirmation question sent to user."},
                        )
                    ))

                elif "__ask_user__" in result:
                    col     = result.get("collection") or (pending or {}).get("collection", "")
                    partial = result.get("partial_document") or (pending or {}).get("insert_document", {})
                    if col or partial:
                        self._next_pending = {"collection": col, "insert_document": partial}
                    early_return = result["__ask_user__"]
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": "Question forwarded to user."},
                        )
                    ))

                else:
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response=result,
                        )
                    ))

            if early_return is not None:
                return early_return

            contents.append(types.Content(role="user", parts=tool_response_parts))

        return "I wasn't able to complete that request. Please try again."

    def close(self):
        self.mongo_client.close()
