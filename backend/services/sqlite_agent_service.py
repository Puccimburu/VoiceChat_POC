"""SQLite Query Agent — agentic: Gemini function calling + ReAct loop."""
import json
import random
import time
import sqlite3
from datetime import datetime
from google import genai
from google.genai import types
from config import GEMINI_API_KEY

_gemini      = genai.Client(api_key=GEMINI_API_KEY)
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


class SQLiteAgent:

    def __init__(self, db_config: dict):
        self.gemini      = _gemini
        self.db_path     = db_config["db_path"]
        self.schema_desc = db_config.get("schema_description", "")
        if not __import__("os").path.exists(self.db_path):
            raise FileNotFoundError(f"SQLite database not found: {self.db_path}")
        self._tools        = self._build_tools()
        self._next_pending = None

    # ── Schema ──────────────────────────────────────────────────────────

    def _schema(self) -> str:
        entry = _schema_store.get(self.db_path)
        if entry and (time.time() - entry["ts"]) < _SCHEMA_TTL:
            return entry["schema"]
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row[0] for row in cur.fetchall()]
            parts  = []
            for table in tables:
                cur.execute(f"PRAGMA table_info({table})")
                cols      = cur.fetchall()
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

    def _invalidate_schema(self):
        _schema_store.pop(self.db_path, None)

    # ── Tool definitions ────────────────────────────────────────────────

    def _build_tools(self) -> types.Tool:
        return types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="query_table",
                description=(
                    "Run a SQLite SELECT query. Use for questions, lookups, listings, and counts. "
                    "If 0 rows returned, retry with a broader LIKE filter before reporting nothing found. "
                    "Always LIMIT 20 unless counting."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "sql": types.Schema(type=types.Type.STRING,
                                            description="Valid SQLite SELECT statement"),
                    },
                    required=["sql"],
                ),
            ),
            types.FunctionDeclaration(
                name="confirm_action",
                description=(
                    "REQUIRED before any insert_row, update_row, or delete_row call. "
                    "Presents the full action details to the user for confirmation. "
                    "Do NOT call any write tool in the same turn — wait for the user's reply."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "summary":      types.Schema(type=types.Type.STRING,
                                                     description="Natural-language summary of what will happen"),
                        "action_type":  types.Schema(type=types.Type.STRING,
                                                     description="insert | update | delete"),
                        "table":        types.Schema(type=types.Type.STRING),
                        "values":       types.Schema(type=types.Type.OBJECT,
                                                     description="Row values to insert (action_type=insert)"),
                        "where_sql":    types.Schema(type=types.Type.STRING,
                                                     description="WHERE clause for update/delete (without the WHERE keyword)"),
                        "update_set":   types.Schema(type=types.Type.OBJECT,
                                                     description="Column→value mapping to set (action_type=update)"),
                    },
                    required=["summary", "action_type", "table"],
                ),
            ),
            types.FunctionDeclaration(
                name="insert_row",
                description=(
                    "Insert a new row. Call ONLY after confirm_action was accepted by the user. "
                    "NEVER include system fields: *_id, status, created_at, source, price/fee/amount."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "table":  types.Schema(type=types.Type.STRING),
                        "values": types.Schema(type=types.Type.OBJECT,
                                               description="Column→value mapping (user-facing fields only)"),
                    },
                    required=["table", "values"],
                ),
            ),
            types.FunctionDeclaration(
                name="update_row",
                description=(
                    "Update an existing row. Call ONLY after confirm_action was accepted. "
                    "Requires a WHERE clause."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "table":      types.Schema(type=types.Type.STRING),
                        "where_sql":  types.Schema(type=types.Type.STRING,
                                                   description="WHERE clause without the WHERE keyword, e.g. \"member_name='Charlotte' AND date='2026-02-25'\""),
                        "update_set": types.Schema(type=types.Type.OBJECT,
                                                   description="Column→value mapping of fields to change"),
                    },
                    required=["table", "where_sql", "update_set"],
                ),
            ),
            types.FunctionDeclaration(
                name="delete_row",
                description=(
                    "Delete a row. Call ONLY after confirm_action was accepted. "
                    "Requires a WHERE clause — will never delete all rows."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "table":     types.Schema(type=types.Type.STRING),
                        "where_sql": types.Schema(type=types.Type.STRING,
                                                  description="WHERE clause without the WHERE keyword"),
                    },
                    required=["table", "where_sql"],
                ),
            ),
            types.FunctionDeclaration(
                name="ask_user",
                description=(
                    "Ask the user for missing information (missing field, ambiguous name). "
                    "Do NOT use this for confirmation — use confirm_action instead."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "question":       types.Schema(type=types.Type.STRING),
                        "table":          types.Schema(type=types.Type.STRING,
                                                       description="Target table if an insert is in progress"),
                        "partial_values": types.Schema(type=types.Type.OBJECT,
                                                       description="Column values collected so far"),
                    },
                    required=["question"],
                ),
            ),
        ])

    # ── Tool execution ──────────────────────────────────────────────────

    def _do_query(self, args: dict) -> dict:
        sql = args.get("sql", "").strip()
        if not sql.upper().startswith("SELECT"):
            return {"error": "Only SELECT statements are allowed via query_table."}
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows    = conn.execute(sql).fetchall()
            results = [dict(row) for row in rows]
            return {"count": len(results), "results": results}
        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    def _do_insert(self, args: dict) -> dict:
        table  = args.get("table", "")
        values = dict(args.get("values") or {})
        if not table or not values:
            return {"error": "Missing table or values for insert."}
        values.setdefault("status",     "confirmed")
        values.setdefault("created_at", datetime.now().isoformat())
        values.setdefault("source",     "voice")
        cols         = ", ".join(values.keys())
        placeholders = ", ".join("?" * len(values))
        sql  = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(sql, list(values.values()))
            conn.commit()
            self._invalidate_schema()
            return {"success": True, "table": table, "document": values}
        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    def _do_update(self, args: dict) -> dict:
        table      = args.get("table", "")
        where_sql  = args.get("where_sql", "").strip()
        update_set = dict(args.get("update_set") or {})
        if not table or not where_sql or not update_set:
            return {"error": "Missing table, where_sql, or update_set for update."}
        set_clause = ", ".join(f"{col} = ?" for col in update_set)
        sql  = f"UPDATE {table} SET {set_clause} WHERE {where_sql}"
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(sql, list(update_set.values()))
            conn.commit()
            self._invalidate_schema()
            return {"success": cur.rowcount > 0, "rows_updated": cur.rowcount, "table": table}
        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    def _do_delete(self, args: dict) -> dict:
        table     = args.get("table", "")
        where_sql = args.get("where_sql", "").strip()
        if not table or not where_sql:
            return {"error": "Missing table or where_sql for delete."}
        sql  = f"DELETE FROM {table} WHERE {where_sql}"
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(sql)
            conn.commit()
            self._invalidate_schema()
            return {"success": cur.rowcount > 0, "rows_deleted": cur.rowcount, "table": table}
        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    @staticmethod
    def _build_confirm_summary(action_type: str, table: str,
                                values: dict, where_sql: str, update_set: dict) -> str:
        """Fallback summary when Gemini omits the summary arg."""
        col = table.rstrip("s")

        if action_type == "update":
            new_vals = ", ".join(f"{k} to {v}" for k, v in (update_set or {}).items())
            parts = [f"Update {col}"]
            if where_sql: parts.append(f"where {where_sql}")
            if new_vals:  parts.append(f"— changing {new_vals}")
            return " ".join(parts) + ". Shall I go ahead?"

        if action_type == "delete":
            parts = [f"Delete {col}"]
            if where_sql: parts.append(f"where {where_sql}")
            return " ".join(parts) + ". Shall I proceed?"

        if action_type == "insert":
            name = (values or {}).get("member_name") or (values or {}).get("name", "")
            parts = [f"Add new {col}{' for ' + name if name else ''}"]
            return " ".join(parts) + ". Shall I proceed?"

        return f"Perform {action_type} on {table}. Shall I proceed?"

    def _run_tool(self, name: str, args: dict) -> dict:
        if name == "query_table":   return self._do_query(args)
        if name == "insert_row":    return self._do_insert(args)
        if name == "update_row":    return self._do_update(args)
        if name == "delete_row":    return self._do_delete(args)
        if name == "confirm_action":
            summary = (args.get("summary") or "").strip()
            if not summary:
                summary = self._build_confirm_summary(
                    args.get("action_type", ""),
                    args.get("table", ""),
                    args.get("values") or {},
                    args.get("where_sql", ""),
                    args.get("update_set") or {},
                )
            return {
                "__confirm__": summary,
                "action_type": args.get("action_type", ""),
                "table":       args.get("table", ""),
                "values":      args.get("values") or {},
                "where_sql":   args.get("where_sql", ""),
                "update_set":  args.get("update_set") or {},
            }
        if name == "ask_user":
            return {
                "__ask_user__":  args.get("question", ""),
                "table":         args.get("table", ""),
                "partial_values": args.get("partial_values") or {},
            }
        return {"error": f"Unknown tool '{name}'"}

    # ── Entry point ─────────────────────────────────────────────────────

    def query(self, user_query: str, history: list = None, pending: dict = None) -> str:
        self._next_pending = None
        today = datetime.now().strftime("%Y-%m-%d")

        system = (
            f"You are a voice assistant for {self.schema_desc or 'a business'}. "
            "Use your tools to answer questions and process requests.\n\n"
            f"SCHEMA:\n{self._schema()}\n\n"
            f"TODAY: {today}\n\n"
            "Rules:\n"
            "- ALWAYS call confirm_action before insert_row, update_row, or delete_row\n"
            "- Do NOT call any write tool in the same turn as confirm_action — wait for user reply\n"
            "- 'change', 'update', 'switch', 'move', 'reschedule', 'amend' → use update_row (NOT insert)\n"
            "- 'cancel', 'remove', 'delete' → use delete_row (NOT insert)\n"
            "- Only use insert_row for brand-new records that do not already exist\n"
            "- For inserts: never include system fields (*_id, status, created_at, source, prices/fees/amounts)\n"
            "- Match user input to exact column values from the schema\n"
            "- If query_table returns 0 rows, retry with a broader LIKE filter before giving up\n"
            "- You may chain tools (e.g. query to find the existing record before updating it)\n"
            "- If the user's message is unclear or garbled, ask for clarification — never return empty text.\n"
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
            table       = pending.get("table", "")
            summary     = pending.get("confirmation_summary", "the pending action")
            if action_type == "insert":
                payload = json.dumps(pending.get("values", {}))
            elif action_type == "update":
                payload = f"where={pending.get('where_sql', '')}, set={json.dumps(pending.get('update_set', {}))}"
            elif action_type == "delete":
                payload = f"where={pending.get('where_sql', '')}"
            else:
                payload = ""
            write_tool = {"insert": "insert_row", "update": "update_row", "delete": "delete_row"}.get(action_type, action_type)
            user_text = (
                f"{user_query}\n"
                f"[AWAITING CONFIRMATION — {summary}. "
                f"Action: {action_type} on '{table}'. Details: {payload}. "
                f"If the user confirmed (yes / correct / go ahead / sure), call {write_tool} with EXACTLY these details. "
                f"If declined (no / cancel / stop), tell them the action was cancelled and do not write anything.]"
            )
        else:
            user_text = user_query
            if pending:
                user_text += f"\n[INSERT IN PROGRESS — fields so far: {json.dumps(pending.get('insert_values', {}))}]"

        contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[self._tools],
            temperature=0.1,
        )

        # ReAct loop
        for i in range(MAX_LOOP):
            print(f"[SQLiteAgent] Loop {i + 1}")
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
                )
                return text.strip() or "Sorry, I didn't quite catch that. Could you say that again?"

            tool_response_parts = []
            early_return        = None

            for fc in function_calls:
                args   = dict(fc.args) if fc.args else {}
                result = self._run_tool(fc.name, args)
                print(f"[SQLiteAgent] {fc.name}({args}) -> {result}")

                if "__confirm__" in result:
                    self._next_pending = {
                        "awaiting_confirmation":  True,
                        "confirmation_summary":   result["__confirm__"],
                        "action_type":            result["action_type"],
                        "table":                  result["table"],
                        "values":                 result["values"],
                        "where_sql":              result["where_sql"],
                        "update_set":             result["update_set"],
                    }
                    early_return = result["__confirm__"]
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": "Confirmation question sent to user."},
                        )
                    ))

                elif "__ask_user__" in result:
                    table   = result.get("table") or (pending or {}).get("table", "")
                    partial = result.get("partial_values") or (pending or {}).get("insert_values", {})
                    if table or partial:
                        self._next_pending = {"table": table, "insert_values": partial}
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
        pass
