"""SQLite Query Agent — multi-tenant, generic: queries any SQLite database."""
import json
import time
import sqlite3
from datetime import datetime
from google import genai
from google.genai import types
from config import GEMINI_API_KEY

_gemini      = genai.Client(api_key=GEMINI_API_KEY)
_SCHEMA_TTL  = 300  # seconds — schema rebuilt at most every 5 minutes
_schema_store: dict = {}  # db_path → {"schema": str, "ts": float}


class SQLiteAgent:
    """
    Multi-tenant voice agent that queries ANY SQLite database.

    Drop-in replacement for MongoDBAgent when db_config['type'] == 'sqlite'.
    db_config must contain:
        db_path            — absolute path to the .sqlite / .db file on the server
        schema_description — optional plain-English hint about the data and write operations
    """

    def __init__(self, db_config: dict):
        self.gemini      = _gemini
        self.db_path     = db_config["db_path"]
        self.schema_desc = db_config.get("schema_description", "")

        if not __import__("os").path.exists(self.db_path):
            raise FileNotFoundError(f"SQLite database not found: {self.db_path}")

    # ── Helpers ────────────────────────────────────────────────────────

    def _schema(self) -> str:
        """Return schema string, rebuilding from the DB at most every _SCHEMA_TTL seconds.

        Stored in module-level _schema_store keyed by db_path so it survives
        agent re-instantiation (fresh agent per request, no connection issues).
        """
        entry = _schema_store.get(self.db_path)
        if entry and (time.time() - entry["ts"]) < _SCHEMA_TTL:
            return entry["schema"]

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row[0] for row in cur.fetchall()]

            parts = []
            for table in tables:
                cur.execute(f"PRAGMA table_info({table})")
                cols      = cur.fetchall()   # (cid, name, type, notnull, default, pk)
                col_str   = ", ".join(f"{c[1]} {c[2]}" for c in cols)
                name_cols = [c[1] for c in cols if "name" in c[1].lower() or c[1] == "title"]
                vals = []
                for col in name_cols:
                    cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT 50")
                    vals.extend([str(r[0]) for r in cur.fetchall() if r[0]])
                line = f"  {table}({col_str})"
                if vals:
                    line += f" values={vals}"
                parts.append(line)
        finally:
            conn.close()

        base   = "Tables:\n" + "\n".join(parts)
        schema = f"{self.schema_desc}\n\n{base}" if self.schema_desc else base
        _schema_store[self.db_path] = {"schema": schema, "ts": time.time()}
        return schema

    def _llm(self, prompt: str, temperature: float = 0.7, json_mode: bool = False) -> str:
        """Single Gemini call used by all methods."""
        cfg = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        return self.gemini.models.generate_content(
            model="gemini-2.5-flash-lite", contents=prompt, config=cfg
        ).text.strip()

    # ── Planning ───────────────────────────────────────────────────────

    def _plan(self, user_query: str, history: list = None, pending: dict = None) -> dict:
        """Gemini call #1: natural language → structured operation plan (JSON)."""
        today = datetime.now().strftime("%Y-%m-%d")

        pending_block = ""
        if pending:
            table = pending.get("table", "")
            lines = [
                f'  {k} = "{v}"' if v else f'  {k} = (MISSING — extract from message)'
                for k, v in pending.get("insert_values", {}).items()
            ]
            pending_block = (
                f"\nINSERT IN PROGRESS — return operation_type=\"insert\", insert_table=\"{table}\".\n"
                f"Known fields:\n" + "\n".join(lines) + "\n"
                "Fill MISSING fields from the message. Keep known fields unchanged.\n"
            )

        history_block = ""
        if history and not pending:
            lines = [
                f"User: {t.get('user', '')}\nAssistant: {t.get('assistant', '')}"
                for t in history[-4:]
            ]
            history_block = "\nCONVERSATION HISTORY:\n" + "\n".join(lines) + "\n"

        prompt = f"""You are a SQLite database assistant for {self.schema_desc or "a business"}.

SCHEMA:
{self._schema()}

TODAY: {today}
{history_block}
Use EXACT values from the schema. USER QUERY: "{user_query}"
{pending_block}
Return JSON:
{{
  "intent": "",
  "operation_type": "read",
  "sql": "SELECT ...",
  "insert_table": "",
  "insert_values": {{}},
  "ready_to_insert": false,
  "ask_user": ""
}}

operation_type "read" = questions/lookups/listing. "insert" = adding/creating/booking/ordering.

READ: write valid SQLite SELECT SQL. Use LIKE '%x%' for text search. Always LIMIT 20. Set sql, leave insert_* empty.

INSERT rules:
- Only collect fields the user would naturally know: names, dates, times, quantities, descriptions.
- NEVER include system fields in insert_values — identify them by these patterns:
    * Any field whose name ends with "_id" — these are system-generated keys
    * Any field named: status, source, created_at, updated_at, created, updated — set by the system
    * Any monetary field: amount, price, cost, rate, fee, total — looked up or calculated by the system
- If a person's name was provided but does NOT clearly match any name in the schema values, set ready_to_insert=false and ask_user="Did you mean [closest match]? Please confirm the name."
- Set ready_to_insert=true only when all USER-FACING required fields have values.
- If any user-facing field is missing, set ready_to_insert=false and write a short, friendly ask_user.
- Dates → YYYY-MM-DD (today={today}). Leave sql empty for inserts."""

        try:
            return json.loads(self._llm(prompt, temperature=0.1, json_mode=True))
        except Exception as e:
            print(f"[SQLiteAgent] Plan error: {e}")
            return {"intent": "error", "operation_type": "read", "sql": None}

    # ── Execution ──────────────────────────────────────────────────────

    def _execute(self, plan: dict):
        """Execute a read query or parameterized insert depending on operation_type."""
        op = plan.get("operation_type", "read")

        if op == "insert":
            table  = plan.get("insert_table", "")
            values = plan.get("insert_values", {})
            if not table or not values:
                return {"success": False, "error": "Missing table or values for insert"}
            values.setdefault("status",     "confirmed")
            values.setdefault("created_at", datetime.now().isoformat())
            values.setdefault("source",     "voice")
            cols        = ", ".join(values.keys())
            placeholders = ", ".join("?" * len(values))
            sql  = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(sql, list(values.values()))
                conn.commit()
                return {"success": True, "table": table, "document": values}
            except Exception as e:
                return {"success": False, "error": str(e)}
            finally:
                conn.close()

        # READ
        sql = plan.get("sql")
        if not sql:
            return []
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"[SQLiteAgent] Query error: {e} | SQL: {sql}")
            return []
        finally:
            conn.close()

    # ── Formatting ─────────────────────────────────────────────────────

    def _speak(self, context: str) -> str:
        """Gemini call #2: turn any data context into a natural spoken response."""
        try:
            return self._llm(f"Generate a natural, concise voice response. No markdown.\n\n{context}")
        except Exception:
            return "Done."

    # ── Entry point ────────────────────────────────────────────────────

    def query(self, user_query: str, history: list = None, pending: dict = None) -> str:
        """Natural language in → natural language out.

        Sets self._next_pending:
          None → no insert in progress.
          dict → partial insert to carry into the next turn.
        """
        self._next_pending = None
        print(f"\n[SQLiteAgent] Query: {user_query}")

        plan = self._plan(user_query, history=history, pending=pending)
        print(f"[SQLiteAgent] Intent: {plan.get('intent')} | Op: {plan.get('operation_type')}")

        if plan.get("operation_type") == "insert":
            # Merge fields already collected in previous turns
            if pending:
                vals = plan.get("insert_values") or {}
                for k, v in (pending.get("insert_values") or {}).items():
                    if not vals.get(k) and v:
                        vals[k] = v
                plan["insert_values"] = vals
                if not plan.get("insert_table"):
                    plan["insert_table"] = pending.get("table", "")

            if not plan.get("ready_to_insert"):
                response = plan.get("ask_user") or "Could you provide the missing details?"
                if plan.get("insert_table"):
                    self._next_pending = {
                        "table":         plan["insert_table"],
                        "insert_values": plan.get("insert_values", {}),
                    }
            else:
                result = self._execute(plan)
                print(f"[SQLiteAgent] Insert: {result}")
                response = (
                    self._speak(
                        f"Confirmed insert into '{result['table']}'.\n"
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
            print(f"[SQLiteAgent] Rows: {len(results)}")
            response = self._speak(
                f'User asked: "{user_query}"\nIntent: {plan.get("intent")}\n'
                f"Total found: {len(results)}\nData: {json.dumps(results[:20], indent=2)}"
            )
            if pending:
                self._next_pending = pending

        print(f"[SQLiteAgent] Response: {response}\n")
        return response

    def close(self):
        pass  # Connections are opened/closed per query
