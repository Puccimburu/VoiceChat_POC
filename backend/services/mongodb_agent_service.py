"""MongoDB Query Agent - Multi-tenant: queries any customer's database dynamically."""
import json, time
from datetime import datetime, timedelta
from pymongo import MongoClient
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, PLATFORM_MONGO_URI, PLATFORM_DB

_gemini     = genai.Client(api_key=GEMINI_API_KEY)
_BLOCKED    = {"admin", "api_keys", "customers"}
_SCHEMA_TTL = 300
_schema_store: dict = {}


class MongoDBAgent:

    def __init__(self, db_config: dict = None):
        self.gemini = _gemini
        cfg              = db_config or {}
        conn_str         = cfg.get("connection_string", PLATFORM_MONGO_URI)
        db_name          = cfg.get("database", PLATFORM_DB)
        self.schema_desc = cfg.get("schema_description", "")
        cols             = cfg.get("collections", [])
        self.mongo_client = MongoClient(conn_str)
        self.db           = self.mongo_client[db_name]
        self._cache_key   = (conn_str, db_name)
        self.collections  = cols or [c for c in self.db.list_collection_names() if c not in _BLOCKED]

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

    def _llm(self, prompt: str, temperature: float = 0.7, json_mode: bool = False) -> str:
        cfg = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        return self.gemini.models.generate_content(
            model="gemini-2.5-flash-lite", contents=prompt, config=cfg
        ).text.strip()

    def _plan(self, user_query: str, history: list = None, pending: dict = None) -> dict:
        today    = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        pending_block = ""
        if pending:
            col   = pending.get("collection", "")
            lines = [
                f'  {k} = "{v}"' if v else f'  {k} = (MISSING — extract from message)'
                for k, v in pending.get("insert_document", {}).items()
            ]
            pending_block = (
                f"\nINSERT IN PROGRESS — return operation_type=\"insert\", collection=\"{col}\".\n"
                f"Known fields:\n" + "\n".join(lines) + "\n"
                "Fill MISSING fields from the message. Keep known fields unchanged.\n"
            )

        history_block = ""
        if history and not pending:
            lines = [f"User: {t.get('user', '')}\nAssistant: {t.get('assistant', '')}" for t in history[-4:]]
            history_block = "\nCONVERSATION HISTORY:\n" + "\n".join(lines) + "\n"

        prompt = f"""You are a database assistant for {self.schema_desc or "a business"}.

SCHEMA:
{self._schema()}

TODAY: {today} | TOMORROW: {tomorrow}
COLLECTIONS: {self.collections}
{history_block}
Use EXACT values from the schema. USER QUERY: "{user_query}"
{pending_block}
Return JSON:
{{
  "intent": "", "operation_type": "read", "collection": "",
  "query": {{}}, "limit": 10, "needs_aggregation": false, "aggregation_pipeline": [],
  "insert_document": {{}}, "ready_to_insert": false, "ask_user": ""
}}

operation_type "read" = questions/lookups/listing. "insert" = booking/adding/creating/ordering.

INSERT rules:
- Only collect fields the user would naturally know: names, dates, times, quantities, descriptions.
- NEVER include system fields in insert_document — identify them by these patterns:
    * Any field whose name ends with "_id" — these are system-generated keys
    * Any field named: status, source, created_at, updated_at, created, updated — set by the system
    * Any monetary field: amount, price, cost, rate, fee, total — looked up or calculated by the system
- Use actual_values from the schema to fill exact names (e.g. match user input to closest value shown).
- If a person's name was provided but does NOT clearly match any name in the schema values, set ready_to_insert=false and ask_user="Did you mean [closest match]? Please confirm the name."
- Set ready_to_insert=true only when all USER-FACING required fields have values.
- If any user-facing field is missing, set ready_to_insert=false and write a short, friendly ask_user.
- Dates → YYYY-MM-DD (today={today}, tomorrow={tomorrow}). Times → HH:MM-HH:MM.

READ: limit 5-10 for lists, 0 for counts. Regex: {{"f": {{"$regex": "x", "$options": "i"}}}}."""

        try:
            return json.loads(self._llm(prompt, temperature=0.1, json_mode=True))
        except Exception as e:
            print(f"Plan error: {e}")
            return {"intent": "error", "operation_type": "read", "collection": None, "query": {}}

    def _execute(self, plan: dict):
        col_name = plan.get("collection")
        op       = plan.get("operation_type", "read")
        if op == "insert":
            if col_name in _BLOCKED:
                return {"success": False, "error": f"Insert not allowed for '{col_name}'"}
            doc = dict(plan.get("insert_document", {}))
            doc.setdefault("status",     "confirmed")
            doc.setdefault("created_at", datetime.now().isoformat())
            doc.setdefault("source",     "voice")
            try:
                self.db[col_name].insert_one(doc)
                doc.pop("_id", None)
                return {"success": True, "collection": col_name, "document": doc}
            except Exception as e:
                return {"success": False, "error": str(e)}
        if not col_name or col_name not in self.collections:
            return []
        try:
            col = self.db[col_name]
            if plan.get("needs_aggregation"):
                results = list(col.aggregate(plan.get("aggregation_pipeline", [])))
            else:
                cursor = col.find(plan.get("query", {}))
                if plan.get("limit", 0) > 0:
                    cursor = cursor.limit(plan["limit"])
                results = list(cursor)
            for d in results:
                if "_id" in d:
                    d["_id"] = str(d["_id"])
            return results
        except Exception as e:
            print(f"Query error: {e}")
            return []

    def _speak(self, context: str) -> str:
        try:
            return self._llm(f"Generate a natural, concise voice response. No markdown.\n\n{context}")
        except Exception:
            return "Done."

    def query(self, user_query: str, history: list = None, pending: dict = None) -> str:
        self._next_pending = None
        print(f"\nQuery: {user_query}")
        plan = self._plan(user_query, history=history, pending=pending)
        print(f"Intent: {plan.get('intent')} | Op: {plan.get('operation_type')} | Col: {plan.get('collection')}")

        if plan.get("operation_type") == "insert":
            if pending:
                doc = plan.get("insert_document") or {}
                for k, v in (pending.get("insert_document") or {}).items():
                    if not doc.get(k) and v:
                        doc[k] = v
                plan["insert_document"] = doc
                if not plan.get("collection"):
                    plan["collection"] = pending.get("collection", "")
            if not plan.get("ready_to_insert"):
                response = plan.get("ask_user") or "Could you provide the missing details?"
                if plan.get("collection"):
                    self._next_pending = {"collection": plan["collection"], "insert_document": plan.get("insert_document", {})}
            else:
                result = self._execute(plan)
                print(f"Insert: {result}")
                response = (
                    self._speak(
                        f"Confirmed insert into '{result['collection']}'.\n"
                        "Speak a natural 1-2 sentence confirmation. ALWAYS include: "
                        "the person's name (any name/member_name/customer_name field), "
                        "what was booked or created, and date/time if present.\n"
                        "Document:\n"
                        + json.dumps(result["document"], indent=2)
                    ) if result.get("success")
                    else f"I wasn't able to complete that. {result.get('error', 'Please try again.')}"
                )
        else:
            results = self._execute(plan)
            print(f"Results: {len(results)} docs")
            response = self._speak(
                f'User asked: "{user_query}"\nIntent: {plan.get("intent")}\n'
                f"Total found: {len(results)}\nData: {json.dumps(results[:50], indent=2)}"
            )
            if pending:
                self._next_pending = pending

        print(f"Response: {response}\n")
        return response

    def close(self):
        self.mongo_client.close()
