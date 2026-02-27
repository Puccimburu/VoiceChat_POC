"""MongoDB Query Agent — agentic: Gemini function calling + ReAct loop."""
import json
import random
import time
from datetime import datetime, timedelta
from pymongo import MongoClient
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, PLATFORM_MONGO_URI, PLATFORM_DB

_gemini      = genai.Client(api_key=GEMINI_API_KEY)
_BLOCKED     = {"admin", "api_keys", "customers"}
_SCHEMA_TTL  = 300
_schema_store: dict = {}
MAX_LOOP     = 10
_MAX_RETRIES = 4


def _gemini_call(client, contents, config, model="gemini-2.5-flash-lite"):
    """Call Gemini with exponential backoff on transient errors (503, SSL corruption)."""
    for attempt in range(_MAX_RETRIES):
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as e:
            msg = str(e)
            is_retryable = (
                "503" in msg
                or "UNAVAILABLE" in msg
                or "overload" in msg.lower()
                or "SSLV3" in msg
                or "BAD_RECORD_MAC" in msg
                or "SSL" in msg
            )
            if is_retryable and attempt < _MAX_RETRIES - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                print(f"[Gemini] transient error ({msg[:60]}), retry {attempt + 1}/{_MAX_RETRIES - 1} in {wait:.1f}s")
                time.sleep(wait)
            else:
                raise


class MongoDBAgent:

    def __init__(self, db_config: dict = None):
        self.gemini       = _gemini
        cfg               = db_config or {}
        conn_str          = cfg.get("connection_string", PLATFORM_MONGO_URI)
        db_name           = cfg.get("database", PLATFORM_DB)
        self.schema_desc  = cfg.get("schema_description", "")
        cols              = cfg.get("collections", [])
        self.mongo_client = MongoClient(conn_str)
        self.db           = self.mongo_client[db_name]
        self._cache_key   = (conn_str, db_name)
        self.collections  = cols or [c for c in self.db.list_collection_names() if c not in _BLOCKED]
        self._tools                    = self._build_tools()
        self._next_pending             = None
        self._last_queried_for_booking = None   # tracks most-recent single-result facility/class lookup
        self._last_class_result        = None   # full doc for fallback spoken response if model goes empty
        self._last_booking_result      = None   # full doc for fallback when model goes empty after bookings query

    # ── Schema ──────────────────────────────────────────────────────────

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

    # ── Tool definitions ────────────────────────────────────────────────

    def _build_tools(self) -> types.Tool:
        obj = types.Schema(type=types.Type.OBJECT)
        return types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="query_collection",
                description=(
                    "Read documents from a MongoDB collection. Use for questions, lookups, "
                    "listings, and counts. If 0 results are returned, retry with a broader "
                    "filter before reporting nothing was found."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "collection": types.Schema(type=types.Type.STRING),
                        "filter":     types.Schema(type=types.Type.OBJECT,
                                                   description="MongoDB filter — use {} for all docs"),
                        "limit":      types.Schema(type=types.Type.INTEGER,
                                                   description="Max docs to return (0 = no limit)"),
                        "aggregation_pipeline": types.Schema(
                            type=types.Type.ARRAY, items=obj,
                            description="Pipeline stages for grouping, sorting, computed fields",
                        ),
                    },
                    required=["collection"],
                ),
            ),
            types.FunctionDeclaration(
                name="confirm_action",
                description=(
                    "REQUIRED before any insert_document, update_document, or delete_document call. "
                    "Presents the full action details to the user for confirmation. "
                    "Do NOT call any write tool in the same turn — wait for the user's reply."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "summary":     types.Schema(type=types.Type.STRING,
                                                    description="First-person confirmation question spoken to the user. "
                                                                "Example: 'I'd like to book the Main Gym for Oliver on Monday at 5 PM. Shall I go ahead?'"),
                        "action_type": types.Schema(type=types.Type.STRING,
                                                    description="insert | update | delete"),
                        "collection":  types.Schema(type=types.Type.STRING),
                        "document":    types.Schema(type=types.Type.OBJECT,
                                                    description="Document to insert (action_type=insert)"),
                        "filter":      types.Schema(type=types.Type.OBJECT,
                                                    description="Filter to find the record (action_type=update|delete)"),
                        "updates":     types.Schema(type=types.Type.OBJECT,
                                                    description="Fields to change (action_type=update)"),
                    },
                    required=["summary", "action_type", "collection"],
                ),
            ),
            types.FunctionDeclaration(
                name="insert_document",
                description=(
                    "Insert a new document. Call ONLY after confirm_action was accepted by the user. "
                    "NEVER include system fields: _id, status, created_at, source, price/fee/amount."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "collection": types.Schema(type=types.Type.STRING),
                        "document":   types.Schema(type=types.Type.OBJECT,
                                                   description="User-facing fields only"),
                    },
                    required=["collection", "document"],
                ),
            ),
            types.FunctionDeclaration(
                name="update_document",
                description=(
                    "Update an existing document. Call ONLY after confirm_action was accepted. "
                    "Uses $set — only the specified fields are changed."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "collection": types.Schema(type=types.Type.STRING),
                        "filter":     types.Schema(type=types.Type.OBJECT,
                                                   description="Filter to identify the document to update"),
                        "updates":    types.Schema(type=types.Type.OBJECT,
                                                   description="Fields and new values to set"),
                    },
                    required=["collection", "filter", "updates"],
                ),
            ),
            types.FunctionDeclaration(
                name="delete_document",
                description=(
                    "Delete a document. Call ONLY after confirm_action was accepted. "
                    "Requires a non-empty filter — will never delete all documents."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "collection": types.Schema(type=types.Type.STRING),
                        "filter":     types.Schema(type=types.Type.OBJECT,
                                                   description="Filter to identify the document to delete"),
                    },
                    required=["collection", "filter"],
                ),
            ),
            types.FunctionDeclaration(
                name="ask_user",
                description=(
                    "Ask the user for missing information needed to complete the task "
                    "(missing field, ambiguous name). Do NOT use this for confirmation — use confirm_action instead."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "question":         types.Schema(type=types.Type.STRING),
                        "collection":       types.Schema(type=types.Type.STRING,
                                                         description="Target collection if an insert is in progress"),
                        "partial_document": types.Schema(type=types.Type.OBJECT,
                                                         description="Fields collected so far"),
                    },
                    required=["question"],
                ),
            ),
        ])

    # ── Tool execution ──────────────────────────────────────────────────

    def _do_query(self, args: dict) -> dict:
        col_name = args.get("collection")
        if not col_name or col_name not in self.collections:
            return {"error": f"Collection '{col_name}' not available. Use one of: {self.collections}"}
        col = self.db[col_name]
        try:
            pipeline = args.get("aggregation_pipeline")
            if pipeline:
                results = list(col.aggregate(pipeline))
            else:
                cursor = col.find(args.get("filter") or {})
                limit  = args.get("limit", 10)
                if limit and limit > 0:
                    cursor = cursor.limit(limit)
                results = list(cursor)
            for d in results:
                if "_id" in d:
                    d["_id"] = str(d["_id"])

            # Track single-result facility/class lookups so that if the model returns
            # plain text asking for date/time (without using ask_user), we can still
            # preserve the booking context for the next turn.
            if len(results) == 1 and col_name in ("facilities", "classes"):
                name_field = "facility_name" if col_name == "facilities" else "class_name"
                item_name  = results[0].get("name", "")
                if item_name:
                    self._last_queried_for_booking = {
                        "collection": "bookings",
                        "insert_document": {name_field: item_name},
                    }
                if col_name == "classes":
                    self._last_class_result = results[0]   # used for fallback spoken response

            # Track single-result bookings queries so we can synthesize a spoken
            # summary if the model goes empty in Loop 2 (e.g. user says "Yes." with
            # no active pending state and model doesn't know what to do with the data).
            if len(results) == 1 and col_name == "bookings":
                self._last_booking_result = results[0]

            return {"count": len(results), "results": results}
        except Exception as e:
            return {"error": str(e)}

    def _do_insert(self, args: dict) -> dict:
        col_name = args.get("collection")
        if col_name in _BLOCKED:
            return {"error": f"Insert not allowed for '{col_name}'"}
        if not col_name or col_name not in self.collections:
            return {"error": f"Collection '{col_name}' not available"}
        doc = dict(args.get("document") or {})
        doc.setdefault("status",     "confirmed")
        doc.setdefault("created_at", datetime.now().isoformat())
        doc.setdefault("source",     "voice")

        # Capture original names before enrichment
        class_name    = doc.get("class_name")    if col_name == "bookings" else None
        facility_name = doc.get("facility_name") if col_name == "bookings" else None

        # Auto-generate a unique booking ID for all bookings
        if col_name == "bookings":
            doc.setdefault("booking_id", f"CL{int(time.time() * 1000)}")

        # For class bookings: validate schedule, enforce capacity, enrich doc
        if class_name:
            cls = self.db["classes"].find_one({"name": class_name})
            if cls:
                # Day-of-week check
                booking_date = doc.get("booking_date") or doc.get("date", "")
                allowed_days = cls.get("schedule", {}).get("days", [])
                if booking_date and allowed_days:
                    try:
                        dt = datetime.fromisoformat(str(booking_date))
                        day_name = dt.strftime("%A")
                        if day_name not in allowed_days:
                            return {
                                "error": (
                                    f"'{class_name}' does not run on {day_name}s. "
                                    f"Valid days: {', '.join(allowed_days)}."
                                )
                            }
                    except (ValueError, TypeError):
                        pass  # unparseable date — let the LLM handle it

                # Capacity check
                if cls.get("enrolled", 0) >= cls.get("capacity", 0):
                    return {"error": f"'{class_name}' is fully booked (capacity {cls.get('capacity')})."}

                # Enrich to match manual booking format
                class_id = cls.get("class_id", "")
                doc.setdefault("class_id",      class_id)
                doc.setdefault("facility_id",   class_id)
                doc.setdefault("facility_name", f"Class: {class_name}")
                doc.setdefault("booking_type",  "class")
                doc.setdefault("amount",        cls.get("fees", 0))
                # Build time_slot from schedule (e.g. "Monday, Wednesday, Friday · 16:00-17:00")
                schedule = cls.get("schedule", {})
                days_str = ", ".join(schedule.get("days", []))
                time_str = schedule.get("time", "")
                if days_str and time_str:
                    doc.setdefault("time_slot", f"{days_str} · {time_str}")

        # For facility bookings: enrich with facility_id, amount, booking_type
        elif facility_name:
            facility = self.db["facilities"].find_one({"name": facility_name})
            if facility:
                doc.setdefault("facility_id",  facility.get("facility_id", ""))
                doc.setdefault("booking_type", "facility")
                doc.setdefault("amount",       facility.get("rate_per_hour", 0))

        try:
            self.db[col_name].insert_one(doc)
            doc.pop("_id", None)
            if class_name:
                self.db["classes"].update_one({"name": class_name}, {"$inc": {"enrolled": 1}})
            self._invalidate_schema()
            return {"success": True, "collection": col_name, "document": doc}
        except Exception as e:
            return {"error": str(e)}

    def _do_update(self, args: dict) -> dict:
        col_name = args.get("collection")
        filter_  = args.get("filter") or {}
        updates  = args.get("updates") or {}
        if col_name in _BLOCKED:
            return {"error": f"Update not allowed for '{col_name}'"}
        if not col_name or col_name not in self.collections:
            return {"error": f"Collection '{col_name}' not available"}
        if not filter_:
            return {"error": "A filter is required for update"}
        if not updates:
            return {"error": "No updates provided"}
        try:
            # Day-of-week guard for class booking reschedules:
            # if changing booking_date on a bookings record, verify the new date
            # falls on one of the class's scheduled days.
            new_date = updates.get("booking_date")
            if col_name == "bookings" and new_date:
                existing = self.db[col_name].find_one(filter_)
                class_name = (existing or {}).get("class_name")
                if class_name:
                    cls = self.db["classes"].find_one({"name": class_name}, {"schedule": 1, "_id": 0}) or {}
                    allowed_days = cls.get("schedule", {}).get("days", [])
                    if allowed_days:
                        try:
                            dt = datetime.fromisoformat(str(new_date))
                            day_name = dt.strftime("%A")
                            if day_name not in allowed_days:
                                return {
                                    "error": (
                                        f"'{class_name}' does not run on {day_name}s. "
                                        f"Valid days: {', '.join(allowed_days)}. "
                                        f"Please pick a date that falls on one of those days."
                                    )
                                }
                        except (ValueError, TypeError):
                            pass  # unparseable date — let the LLM handle it

            result = self.db[col_name].update_one(filter_, {"$set": updates})
            self._invalidate_schema()
            return {
                "success":  result.modified_count > 0,
                "matched":  result.matched_count,
                "modified": result.modified_count,
                "collection": col_name,
            }
        except Exception as e:
            return {"error": str(e)}

    def _do_delete(self, args: dict) -> dict:
        col_name = args.get("collection")
        filter_  = args.get("filter") or {}
        if col_name in _BLOCKED:
            return {"error": f"Delete not allowed for '{col_name}'"}
        if not col_name or col_name not in self.collections:
            return {"error": f"Collection '{col_name}' not available"}
        if not filter_:
            return {"error": "A filter is required for delete"}
        try:
            # For class booking cancellations: capture class_name before deleting
            class_name = None
            if col_name == "bookings":
                booking = self.db[col_name].find_one(filter_)
                class_name = (booking or {}).get("class_name")

            result = self.db[col_name].delete_one(filter_)

            if result.deleted_count and class_name:
                self.db["classes"].update_one(
                    {"name": class_name, "enrolled": {"$gt": 0}},
                    {"$inc": {"enrolled": -1}}
                )
            self._invalidate_schema()
            return {
                "success": result.deleted_count > 0,
                "deleted": result.deleted_count,
                "collection": col_name,
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _build_confirm_summary(action_type: str, collection: str,
                                filter_: dict, updates: dict, document: dict) -> str:
        """Fallback summary when Gemini omits the summary arg."""
        combined  = {**(filter_ or {}), **(document or {})}
        name       = combined.get("member_name") or combined.get("name", "")
        date       = combined.get("booking_date") or combined.get("date", "")
        slot       = combined.get("time_slot")    or combined.get("time", "")
        class_name = combined.get("class_name", "")
        fac_name   = combined.get("facility_name", "")
        subject    = class_name or fac_name or collection.rstrip("s")

        if action_type == "update":
            new_vals = ", ".join(f"{k} to {v}" for k, v in (updates or {}).items())
            parts = [f"I'd like to update{' ' + name + chr(39) + 's' if name else ''} {subject}"]
            if date: parts.append(f"on {date}")
            if slot: parts.append(f"at {slot}")
            if new_vals: parts.append(f"— changing {new_vals}")
            return " ".join(parts) + ". Shall I go ahead?"

        if action_type == "delete":
            parts = [f"I'd like to cancel{' ' + name + chr(39) + 's' if name else ''} {subject} booking"]
            if date: parts.append(f"on {date}")
            if slot: parts.append(f"at {slot}")
            return " ".join(parts) + ". Shall I proceed?"

        if action_type == "insert":
            parts = [f"I'd like to book {subject}{' for ' + name if name else ''}"]
            if date: parts.append(f"on {date}")
            if slot: parts.append(f"at {slot}")
            return " ".join(parts) + ". Shall I go ahead?"

        return f"I'm about to {action_type} on {collection}. Shall I proceed?"

    @staticmethod
    def _build_class_description(cls: dict) -> str:
        """Synthesize a spoken class-info + day-prompt from a class document.
        Used as a fallback when the model goes empty after a class query."""
        name       = cls.get("name", "this class")
        instructor = cls.get("instructor", "")
        schedule   = cls.get("schedule", {})
        days       = schedule.get("days", [])
        time_str   = schedule.get("time", "")
        capacity   = cls.get("capacity", 0)
        enrolled   = cls.get("enrolled", 0)
        fee        = cls.get("fees", 0)
        spots_left = max(0, capacity - enrolled)

        line = name
        if instructor:
            line += f" is with {instructor}"
        if days and time_str:
            line += f" on {', '.join(days)} at {time_str}"
        elif days:
            line += f" on {', '.join(days)}"
        line += "."

        parts = [line]
        if fee:
            parts.append(f"The fee is {fee}.")
        if capacity:
            word = "spot" if spots_left == 1 else "spots"
            parts.append(f"There {'is' if spots_left == 1 else 'are'} {spots_left} {word} available.")

        if len(days) > 1:
            days_q = " or ".join(days)
            parts.append(f"Which day would you like to book — {days_q}?")
        elif len(days) == 1:
            parts.append(f"Would you like to book for {days[0]}?")
        else:
            parts.append("Which day would you like to book?")

        return " ".join(parts)

    @staticmethod
    def _build_booking_description(bk: dict) -> str:
        """Synthesize a spoken summary of a booking when the model goes empty after
        a bookings query. Tells the user what booking was found and asks what they
        want to do with it."""
        member      = bk.get("member_name", "")
        class_name  = bk.get("class_name", "")
        fac_name    = bk.get("facility_name", "") or ""
        # Strip the "Class: " prefix that is auto-added during insert enrichment
        if fac_name.startswith("Class: "):
            fac_name = ""
        subject     = class_name or fac_name or "booking"
        date_str    = bk.get("booking_date", "")
        time_slot   = bk.get("time_slot", "")

        # Format date nicely if parseable
        display_date = date_str
        if date_str:
            try:
                dt = datetime.fromisoformat(str(date_str))
                display_date = dt.strftime("%A, %B %d").replace(" 0", " ")
            except (ValueError, TypeError):
                pass

        name_part = f"{member}'s" if member else "a"
        line = f"I found {name_part} {subject} booking"
        if display_date:
            line += f" on {display_date}"
        if time_slot:
            line += f" at {time_slot}"
        line += "."
        line += " Would you like to cancel it, reschedule it, or is there something else I can help you with?"
        return line

    _WRITE_ACTION = {
        "insert_document": "insert",
        "update_document": "update",
        "delete_document": "delete",
    }

    def _run_tool(self, name: str, args: dict) -> dict:
        if name == "query_collection":   return self._do_query(args)

        # Auto-intercept: if the LLM calls a write tool without a prior confirm_action,
        # silently redirect to confirm_action (or ask_user for missing info) so the user
        # always gets a proper flow — regardless of whether the LLM followed instructions.
        if name in self._WRITE_ACTION and not self._confirm_action_called and not self._incoming_awaiting:
            action_type = self._WRITE_ACTION[name]
            doc = args.get("document") or {}
            col = args.get("collection", "")

            # Class booking with no date yet — ask for it before confirming
            if action_type == "insert" and col == "bookings" and doc.get("class_name") and not doc.get("booking_date"):
                class_name = doc["class_name"]
                cls = self.db["classes"].find_one({"name": class_name}, {"schedule": 1, "_id": 0}) or {}
                days = ", ".join(cls.get("schedule", {}).get("days", [])) or "scheduled days"
                time = cls.get("schedule", {}).get("time", "")
                schedule_str = f"{days} at {time}" if time else days
                self._next_pending = {"collection": col, "insert_document": doc}
                return {
                    "__ask_user__": f"Which date would you like to book {class_name}? "
                                    f"It runs on {schedule_str}.",
                    "collection": col,
                    "partial_document": doc,
                }

            # Facility booking with no date or time slot — ask for both
            if action_type == "insert" and col == "bookings" and doc.get("facility_name") and not doc.get("booking_date"):
                fac_name = doc["facility_name"]
                self._next_pending = {"collection": col, "insert_document": doc}
                return {
                    "__ask_user__": f"What date and time slot would you like to book the {fac_name}? "
                                    f"For example: Friday at 5 PM.",
                    "collection": col,
                    "partial_document": doc,
                }

            return self._run_tool("confirm_action", {
                "action_type": action_type,
                "collection":  col,
                "document":    doc,
                "filter":      args.get("filter") or {},
                "updates":     args.get("updates") or {},
                "summary":     "",   # built by _build_confirm_summary
            })

        if name == "insert_document":    return self._do_insert(args)
        if name == "update_document":    return self._do_update(args)
        if name == "delete_document":    return self._do_delete(args)
        if name == "confirm_action":
            # Guard: if a confirmation is already awaiting, block re-confirmation
            if self._incoming_awaiting:
                at = self._incoming_action_type or "write"
                return {
                    "error": (
                        f"A confirmation is already in progress — do NOT call confirm_action again. "
                        f"The user has confirmed. Call {at}_document now with the filter/document "
                        f"already shown in the context."
                    )
                }
            # Guard: delete/update must have a real filter — if empty, force a query first
            action_type_check = args.get("action_type", "")
            filter_check      = args.get("filter") or {}
            if action_type_check in ("delete", "update") and not filter_check:
                return {
                    "error": (
                        "Cannot confirm a delete or update without a filter. "
                        "Call query_collection('bookings') first to find the exact record "
                        "using the member's name, then call confirm_action again with the "
                        "real filter values (e.g. member_name + class_name)."
                    )
                }
            self._confirm_action_called = True
            summary = (args.get("summary") or "").strip()
            if not summary:
                summary = self._build_confirm_summary(
                    args.get("action_type", ""),
                    args.get("collection", ""),
                    args.get("filter") or {},
                    args.get("updates") or {},
                    args.get("document") or {},
                )
            return {
                "__confirm__": summary,
                "action_type": args.get("action_type", ""),
                "collection":  args.get("collection", ""),
                "document":    args.get("document") or {},
                "filter":      args.get("filter") or {},
                "updates":     args.get("updates") or {},
            }
        if name == "ask_user":
            return {
                "__ask_user__":    args.get("question", ""),
                "collection":      args.get("collection", ""),
                "partial_document": args.get("partial_document") or {},
            }
        return {"error": f"Unknown tool '{name}'"}

    # ── Entry point ─────────────────────────────────────────────────────

    _DATE_TIME_WORDS = frozenset(["date", "time", "when", "slot", "day", "morning", "afternoon", "evening"])

    def query(self, user_query: str, history: list = None, pending: dict = None) -> str:
        self._next_pending             = None
        self._last_queried_for_booking = None
        self._last_class_result        = None
        self._last_booking_result      = None
        # Guards that prevent write tools from firing without a prior confirm_action
        self._confirm_action_called  = False
        self._incoming_awaiting      = bool(pending and pending.get("awaiting_confirmation"))
        self._incoming_action_type   = (pending.get("action_type", "") if self._incoming_awaiting else "")

        now   = datetime.now()
        today = now.strftime("%Y-%m-%d")

        # Build a 2-week date lookup — model must use these exact values, no arithmetic needed
        _seen_this: set = set()
        _seen_next: set = set()
        _this_lines, _next_lines = [], []
        for delta in range(1, 15):
            d      = now + timedelta(days=delta)
            dname  = d.strftime("%A")
            dval   = d.strftime("%Y-%m-%d")
            if delta == 1:
                _this_lines.insert(0, f"tomorrow ({dname}): {dval}")
                _seen_this.add(dname)
            elif delta <= 7 and dname not in _seen_this:
                _this_lines.append(f"this {dname}: {dval}")
                _seen_this.add(dname)
            elif delta > 7 and dname not in _seen_next:
                _next_lines.append(f"next {dname}: {dval}")
                _seen_next.add(dname)
        date_ref = (
            f"TODAY: {now.strftime('%A')} {today}\n"
            "EXACT DATE LOOKUP — use these values directly, do NOT recalculate:\n  "
            + "\n  ".join(_this_lines + _next_lines)
        )

        system = (
            f"You are a voice assistant for {self.schema_desc or 'a business'}. "
            "Use your tools to answer questions and process requests.\n\n"
            f"SCHEMA:\n{self._schema()}\n\n"
            f"{date_ref}\n"
            "When the user says 'tomorrow', 'this Saturday', 'next Monday', etc., look up the exact date "
            "from the EXACT DATE LOOKUP above — never recalculate.\n"
            f"AVAILABLE COLLECTIONS: {self.collections}\n\n"
            "Rules:\n"
            "- INTENT RECOGNITION (read this first, before all other rules):\n"
            "  * JOINING A CLASS — ANY phrasing that means the user wants to attend, join, enrol in, "
            "book into, or get a spot in a class. Examples: 'enrol me in yoga', 'book for me a slot in yoga', "
            "'get me a spot in yoga', 'I'd like to join yoga', 'put me in yoga', 'reserve me a place in yoga', "
            "'sign me up for yoga', 'add me to yoga', 'I want to do yoga'. → go to CLASS BOOKING FLOW.\n"
            "  * SHORT CLASS REFERENCE — if the user replies with just a class name or short phrase "
            "(e.g. 'swimming', 'the yoga class', 'morning yoga', 'in the swimming class') after you asked "
            "which class they want, treat it as CLASS BOOKING FLOW for that class — do NOT return Sorry.\n"
            "  * BOOKING A FACILITY — user wants to reserve a facility (court, pool, room, etc.) for a time. "
            "→ go to FACILITY BOOKING FLOW.\n"
            "  * CHANGING/RESCHEDULING — user mentions an EXISTING booking and wants to change date/time. "
            "→ go to RESCHEDULING flow.\n"
            "  * CANCELLING — user wants to cancel or remove an existing booking. → use delete_document.\n"
            "  * ABANDONING — if the user says 'forget it', 'never mind', 'skip', 'ignore', 'forget this', "
            "'don't bother', 'leave it', or similar — abandon any pending action and say OK naturally. "
            "Do NOT book anything.\n"
            "  * MULTIPLE SLOTS — if the user asks for N slots, sessions, or bookings in the same class "
            "(e.g. 'I need 3 slots in swimming', 'book me 3 sessions of yoga') — follow MULTI-SLOT BOOKING FLOW.\n"
            "  If you are unsure of intent, ask one short clarifying question. "
            "NEVER return empty text or silence — always either call a tool or speak to the user.\n"
            "- MANDATORY: call confirm_action BEFORE insert_document, update_document, or delete_document — "
            "skipping this step will cause an error and the action will not be saved.\n"
            "- Do NOT call any write tool in the same turn as confirm_action — wait for user reply\n"
            "- 'change', 'update', 'switch', 'move', 'reschedule', 'amend' → use update_document (NOT insert)\n"
            "- 'cancel', 'remove', 'delete' → use delete_document (NOT insert)\n"
            "- Only use insert_document for brand-new records that do not already exist\n"
            "- RESCHEDULING / MODIFYING AN EXISTING BOOKING (HIGHEST PRIORITY — overrides booking flows below):\n"
            "  If the user wants to change a date, time, or time slot on an EXISTING booking of the SAME TYPE "
            "(e.g. change a facility booking date, or change a class booking date):\n"
            "  1. query_collection('bookings') with a filter for member_name to find the existing record.\n"
            "  2. If the user has not yet stated WHICH day or date they want, ask them first "
            "(e.g. 'Which day would you like to move it to?'). Do NOT pick a day on their behalf.\n"
            "     Once the user gives a day name, look up the exact date in the EXACT DATE LOOKUP table — "
            "never derive the new date from the existing booking's date.\n"
            "     Before proposing the new date, verify it falls on one of the class's scheduled days "
            "(from schedule.days). If it does not, tell the user and ask for a valid day.\n"
            "  3. Call confirm_action with action_type='update', showing what will change.\n"
            "  3. Only after the user confirms, call update_document.\n"
            "     CRITICAL: The filter for update_document must NOT include the field being changed. "
            "Use member_name + class_name (or member_name + facility_name) to identify the record — "
            "never put booking_date or time_slot in the filter when those are the fields being updated.\n"
            "  NEVER call insert_document for a same-type reschedule — this would create a duplicate.\n"
            "- CANCEL + REBOOK — applies to TWO situations:\n"
            "  (a) Changing a FACILITY booking to a CLASS booking, or vice versa.\n"
            "  (b) Changing from one CLASS to a DIFFERENT CLASS (e.g. 'switch from Power Yoga to Morning Yoga').\n"
            "  This requires TWO steps. Handle them one at a time:\n"
            "  Step A — query_collection('bookings') to find the existing record, then confirm cancellation "
            "(confirm_action with action_type='delete').\n"
            "  Step B — only after the user confirms the cancellation, start the appropriate booking flow "
            "(CLASS BOOKING FLOW or FACILITY BOOKING FLOW) as a brand-new insert.\n"
            "  NEVER update class_name on an existing booking — this corrupts enrolled counts. Always cancel+rebook.\n"
            "- CLASS ENROLMENT: if the user's message contains ANY enrollment intent — including 'enroll', 'join', "
            "'sign up for', 'take', 'book', 'want', 'reserve', 'get me in', 'put me in', 'add me', "
            "'a slot in', 'a spot in', 'a space in', 'a place in', 'register', or ANY phrase that could mean "
            "they want to attend or join a class — go DIRECTLY to CLASS BOOKING FLOW as a NEW insert. "
            "Do NOT query existing bookings first. Do not attempt to update an existing booking.\n"
            "- For inserts: never include system fields in the document (_id, status, created_at, source). "
            "Never add price, fee, or amount fields to insert documents — these are set by the system.\n"
            "- For READ queries (pricing, availability, info): always use query_collection and return the relevant "
            "field values (e.g. rate_per_hour, capacity, schedule). Never refuse to answer pricing/cost questions — "
            "query the collection and speak the value naturally (e.g. 'Tennis Court 1 costs 18 rupees per hour').\n"
            "- Match user input to exact field values from the schema\n"
            "- If query_collection returns 0 results, retry with a broader filter before giving up\n"
            "- ALWAYS query_collection first before update_document or delete_document to find the exact record. "
            "Use the actual field values from the query result as the filter — never guess field values.\n"
            "- CLASS BOOKING FLOW (follow every step in order):\n"
            "  1. query_collection('classes') to get full class details.\n"
            "  2. After getting the class data, respond with plain text ONLY — do NOT call any tool. "
            "In your spoken response, tell the user: instructor name, days the class runs, time, fee, duration, "
            "spots taken vs available (e.g. '1 of 18 spots filled, 17 remaining'), "
            "then ask which day they would like to book. "
            "Example: 'Power Yoga is with Helen Carter on Saturdays and Sundays at 08:00-09:30. "
            "The fee is 55. There are 17 spots available. Which day would you like — Saturday or Sunday?'\n"
            "     If enrolled >= capacity: class is FULL — do NOT proceed. Instead query_collection('classes') "
            "with no filter, then suggest 1-2 alternatives that still have spots available.\n"
            "  3. Once the user gives a day, look up the exact date in the EXACT DATE LOOKUP table.\n"
            "  4. Validate that the date's day-of-week is in schedule.days.\n"
            "  5. The booking time MUST come from schedule.time — never use a time the user mentions.\n"
            "  6. Call confirm_action with a summary: class name, instructor, date, time (from schedule), "
            "member name, fee, and spots remaining.\n"
            "  7. Only after the user confirms, call insert_document.\n"
            "- CLASS BOOKING FIELDS: when inserting a class booking into 'bookings', the document MUST use "
            "the field 'class_name' (NOT 'facility_name'). Example: {member_id, member_name, class_name, booking_date}.\n"
            "- MULTI-SLOT BOOKING FLOW — when the user wants N sessions of the same class:\n"
            "  1. query_collection('classes') to get the class schedule and fee.\n"
            "  2. From the EXACT DATE LOOKUP table, find the next N dates that match the class's schedule.days. "
            "List all N dates to the user (e.g. 'Saturday March 1st, Saturday March 8th, Saturday March 15th').\n"
            "  3. Call confirm_action ONCE with a summary listing ALL N dates and total cost (fee × N). "
            "Example: 'I'd like to book Swimming for Beginners for Lucy on March 1st, 8th, and 15th at 08:00-09:00. "
            "Total cost: 240. Shall I go ahead?'\n"
            "  4. After the user confirms, call insert_document ONCE FOR EACH DATE as separate calls in the same response. "
            "Each document must have: member_id, member_name, class_name, booking_date (YYYY-MM-DD).\n"
            "  5. After all inserts, confirm how many sessions were booked and the dates.\n"
            "- FACILITY BOOKING FLOW (follow every step in order):\n"
            "  1. query_collection('facilities') to confirm the facility exists and is available.\n"
            "  2. You need BOTH a specific date (YYYY-MM-DD) AND a time slot from the user before you can confirm. "
            "If EITHER is missing, you MUST call the ask_user tool (NOT plain text) to request what is missing. "
            "Do NOT default to today's date — if the user gives only a time, ask for the date too. "
            "Example: ask_user(question='What date would you like? The gym is open 06:00-22:00.', "
            "collection='bookings', partial_document={...}).\n"
            "  3. Call confirm_action with a summary: facility name, date (written as e.g. Monday March 2nd), "
            "time slot, member name, and rate_per_hour (e.g. 'The rate is 18 rupees per hour').\n"
            "  4. Only after the user confirms, call insert_document into 'bookings' with fields: "
            "member_id, member_name, facility_name, booking_date (YYYY-MM-DD), time_slot.\n"
            "- ALWAYS include the cost in every booking confirmation summary — state the fee (classes) or rate_per_hour (facilities) "
            "so the user knows the price before confirming. Never omit cost from a confirmation.\n"
            "- Never repeat the same question twice. If the user's answer is incomplete or invalid, explain why and give them the valid options.\n"
            "- IMPORTANT: Always either call a tool or speak to the user. Never return empty text or silence. "
            "If you are unsure what the user wants, ask for clarification.\n"
            "- Respond naturally for voice: no markdown, no bullet points"
        )

        contents = []
        if history:
            for turn in history[-4:]:
                contents.append(types.Content(role="user",  parts=[types.Part(text=turn["user"])]))
                contents.append(types.Content(role="model", parts=[types.Part(text=turn["assistant"])]))

        # Build user message — handle awaiting_confirmation separately
        if pending and pending.get("awaiting_confirmation"):
            action_type = pending.get("action_type", "action")
            collection  = pending.get("collection", "")
            summary     = pending.get("confirmation_summary", "the pending action")
            if action_type == "insert":
                payload = json.dumps(pending.get("document", {}))
            elif action_type == "update":
                payload = f"filter={json.dumps(pending.get('filter', {}))}, updates={json.dumps(pending.get('updates', {}))}"
            elif action_type == "delete":
                payload = f"filter={json.dumps(pending.get('filter', {}))}"
            else:
                payload = ""
            user_text = (
                f"{user_query}\n"
                f"[AWAITING CONFIRMATION — {summary}. "
                f"Action: {action_type} on '{collection}'. Details: {payload}. "
                f"If the user confirmed (yes / correct / go ahead / sure), call {action_type}_document with EXACTLY these details. "
                f"If the user asks about the schedule, days, or availability (e.g. 'which days does it run?', "
                f"'is it on Sunday too?'), call query_collection to get the accurate schedule from the class/facility "
                f"and answer correctly — do NOT just repeat the date in the pending booking. "
                f"If the user asks about or prefers a different day/time (e.g. 'what about Sunday?', 'can I do Monday?', "
                f"'make it 6 PM'), treat this as a request to change that field. "
                f"Look up the new date in the EXACT DATE LOOKUP table, update that detail in the booking, "
                f"then call confirm_action again with the revised summary — "
                f"do NOT call {action_type}_document yet. "
                f"If declined (no / cancel / stop), tell them the action was cancelled and do not write anything.]"
            )
        else:
            user_text = user_query
            if pending:
                user_text += f"\n[BOOKING IN PROGRESS — fields so far: {json.dumps(pending.get('insert_document', {}))}]"

        contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[self._tools],
            temperature=0.1,
        )

        # ReAct loop
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
                        text = MongoDBAgent._build_class_description(self._last_class_result)
                        print(f"[Agent] Synthesized class fallback for: {self._last_class_result.get('name')}")
                        if not self._next_pending and self._last_queried_for_booking:
                            self._next_pending = self._last_queried_for_booking
                    # Fallback: if the model went empty after a single-booking query
                    # (e.g. user said "Yes." with no active pending state), synthesize
                    # a spoken summary of the booking and ask what to do with it.
                    elif self._last_booking_result:
                        text = MongoDBAgent._build_booking_description(self._last_booking_result)
                        print(f"[Agent] Synthesized booking fallback for: {self._last_booking_result.get('member_name')} / {self._last_booking_result.get('class_name') or self._last_booking_result.get('facility_name')}")

                # Auto-preserve booking context: if the model returned plain text asking
                # for date/time (instead of using ask_user tool), save the last queried
                # facility/class so the next turn still has partial booking context.
                if (not self._next_pending
                        and self._last_queried_for_booking
                        and text
                        and any(w in text.lower() for w in self._DATE_TIME_WORDS)):
                    self._next_pending = self._last_queried_for_booking

                return text or "Sorry, I didn't quite catch that. Could you say that again?"

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
