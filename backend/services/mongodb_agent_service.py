"""MongoDB Query Agent — agentic: Gemini function calling + ReAct loop."""
import json
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
        self._tools        = self._build_tools()
        self._next_pending = None

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
                                                    description="Natural-language summary of what will happen"),
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
        try:
            self.db[col_name].insert_one(doc)
            doc.pop("_id", None)
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
            result = self.db[col_name].delete_one(filter_)
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
        name  = (filter_ or document or {}).get("member_name") or (filter_ or document or {}).get("name", "")
        date  = (filter_ or document or {}).get("booking_date") or (filter_ or document or {}).get("date", "")
        slot  = (filter_ or document or {}).get("time_slot")    or (filter_ or document or {}).get("time", "")
        col   = collection.rstrip("s")

        if action_type == "update":
            new_vals = ", ".join(f"{k} to {v}" for k, v in (updates or {}).items())
            parts = [f"Update{' ' + name + chr(39) + 's' if name else ''} {col}"]
            if date: parts.append(f"on {date}")
            if slot: parts.append(f"at {slot}")
            if new_vals: parts.append(f"— changing {new_vals}")
            return " ".join(parts) + ". Shall I go ahead?"

        if action_type == "delete":
            parts = [f"Cancel{' ' + name + chr(39) + 's' if name else ''} {col}"]
            if date: parts.append(f"on {date}")
            if slot: parts.append(f"at {slot}")
            return " ".join(parts) + ". Shall I proceed?"

        if action_type == "insert":
            parts = [f"Add new {col}{' for ' + name if name else ''}"]
            if date: parts.append(f"on {date}")
            if slot: parts.append(f"at {slot}")
            return " ".join(parts) + ". Shall I proceed?"

        return f"Perform {action_type} on {collection}. Shall I proceed?"

    def _run_tool(self, name: str, args: dict) -> dict:
        if name == "query_collection":   return self._do_query(args)
        if name == "insert_document":    return self._do_insert(args)
        if name == "update_document":    return self._do_update(args)
        if name == "delete_document":    return self._do_delete(args)
        if name == "confirm_action":
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

    def query(self, user_query: str, history: list = None, pending: dict = None) -> str:
        self._next_pending = None
        today    = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        system = (
            f"You are a voice assistant for {self.schema_desc or 'a business'}. "
            "Use your tools to answer questions and process requests.\n\n"
            f"SCHEMA:\n{self._schema()}\n\n"
            f"TODAY: {today} | TOMORROW: {tomorrow}\n"
            f"AVAILABLE COLLECTIONS: {self.collections}\n\n"
            "Rules:\n"
            "- ALWAYS call confirm_action before insert_document, update_document, or delete_document\n"
            "- Do NOT call any write tool in the same turn as confirm_action — wait for user reply\n"
            "- 'change', 'update', 'switch', 'move', 'reschedule', 'amend' → use update_document (NOT insert)\n"
            "- 'cancel', 'remove', 'delete' → use delete_document (NOT insert)\n"
            "- Only use insert_document for brand-new records that do not already exist\n"
            "- For inserts: never include system fields (_id, status, created_at, source, prices/fees/amounts)\n"
            "- Match user input to exact field values from the schema\n"
            "- If query_collection returns 0 results, retry with a broader filter before giving up\n"
            "- You may chain tools (e.g. query to find the existing record before updating it)\n"
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
            response = self.gemini.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=contents,
                config=config,
            )

            model_content = response.candidates[0].content
            contents.append(model_content)

            function_calls = [
                p.function_call for p in model_content.parts
                if hasattr(p, "function_call") and p.function_call
            ]

            if not function_calls:
                text = "".join(
                    p.text for p in model_content.parts
                    if hasattr(p, "text") and p.text
                )
                return text.strip() or "Done."

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
