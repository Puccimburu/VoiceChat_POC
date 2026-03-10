"""AgentToolsMixin — tool dispatch and confirm/ask helpers."""
import logging
from datetime import datetime

from google.genai import types

from .tool_definitions import build_tools
from .descriptions import build_confirm_summary

logger = logging.getLogger("ws_gateway")


class AgentToolsMixin:
    """Mixin providing tool dispatch and confirm/ask flow for MongoDBAgent."""

    # ── Tool definitions ─────────────────────────────────────────────────

    def _build_tools(self) -> types.Tool:
        return build_tools()

    # ── Confirm / ask helpers ─────────────────────────────────────────────

    _WRITE_ACTION = {
        "insert_document": "insert",
        "update_document": "update",
        "delete_document": "delete",
    }

    def _handle_confirm_action(self, args: dict) -> dict:
        """Validate and stage a write action for user confirmation."""
        if self._incoming_awaiting:
            at = self._incoming_action_type or "write"
            return {"error": (
                f"A confirmation is already in progress — do NOT call confirm_action again. "
                f"The user has confirmed. Call {at}_document now with the filter/document "
                f"already shown in the context."
            )}
        action_type = args.get("action_type", "")
        filter_     = args.get("filter") or {}
        if action_type in ("delete", "update") and not filter_:
            return {"error": (
                "Cannot confirm a delete or update without a filter. "
                "Call query_collection('bookings') first to find the exact record "
                "using the member's name, then call confirm_action again with the "
                "real filter values (e.g. member_name + class_name)."
            )}
        # Validate day-of-week for class bookings/reschedules before showing confirmation
        if action_type in ("insert", "update"):
            doc_check   = args.get("document") or {}
            class_check = doc_check.get("class_name") if action_type == "insert" else filter_.get("class_name")
            date_check  = doc_check.get("booking_date") if action_type == "insert" else (args.get("updates") or {}).get("booking_date")
            if class_check and date_check:
                cls_rec  = self.db["classes"].find_one({"name": class_check}, {"schedule": 1, "_id": 0}) or {}
                allowed  = cls_rec.get("schedule", {}).get("days", [])
                if allowed:
                    try:
                        actual_day = datetime.fromisoformat(str(date_check)).strftime("%A")
                        if actual_day not in allowed:
                            return {"error": (
                                f"WRONG DATE: {date_check} is a {actual_day}, not a valid day for '{class_check}'. "
                                f"Valid days: {', '.join(allowed)}. "
                                f"You MUST look up the correct date from the EXACT DATE LOOKUP table in the system prompt "
                                f"and use that exact YYYY-MM-DD value — do NOT calculate or guess."
                            )}
                    except (ValueError, TypeError):
                        pass
        self._confirm_action_called = True
        summary = (args.get("summary") or "").strip()
        if not summary:
            summary = build_confirm_summary(
                action_type, args.get("collection", ""),
                filter_, args.get("updates") or {}, args.get("document") or {},
            )
        return {
            "__confirm__": summary,
            "action_type": action_type,
            "collection":  args.get("collection", ""),
            "document":    args.get("document") or {},
            "filter":      filter_,
            "updates":     args.get("updates") or {},
        }

    # ── Tool dispatcher ───────────────────────────────────────────────────

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
                "summary":     "",   # built by build_confirm_summary
            })

        if name == "insert_document":    return self._do_insert(args)
        if name == "update_document":    return self._do_update(args)
        if name == "delete_document":    return self._do_delete(args)
        if name == "confirm_action":     return self._handle_confirm_action(args)
        if name == "ask_user":
            return {
                "__ask_user__":     args.get("question", ""),
                "collection":       args.get("collection", ""),
                "partial_document": args.get("partial_document") or {},
            }
        return {"error": f"Unknown tool '{name}'"}
