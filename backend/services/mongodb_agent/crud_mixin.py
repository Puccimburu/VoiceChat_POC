"""AgentCrudMixin — MongoDB CRUD operations for MongoDBAgent."""
import logging
import time
from datetime import datetime

from bson import ObjectId

logger = logging.getLogger("ws_gateway")

_BLOCKED = {"admin", "api_keys", "customers"}


class AgentCrudMixin:
    """Mixin providing MongoDB read/write operations."""

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

    def _enrich_class_booking(self, doc: dict, class_name: str, num_spots: int) -> dict | None:
        """Validate and enrich a class booking doc. Returns error dict or None on success."""
        cls = self.db["classes"].find_one({"name": class_name})
        if not cls:
            return None
        booking_date = doc.get("booking_date") or doc.get("date", "")
        allowed_days = cls.get("schedule", {}).get("days", [])
        if booking_date and allowed_days:
            try:
                day_name = datetime.fromisoformat(str(booking_date)).strftime("%A")
                if day_name not in allowed_days:
                    return {"error": f"'{class_name}' does not run on {day_name}s. Valid days: {', '.join(allowed_days)}."}
            except (ValueError, TypeError):
                pass
        if cls.get("enrolled", 0) + num_spots > cls.get("capacity", 0):
            remaining = max(0, cls.get("capacity", 0) - cls.get("enrolled", 0))
            if remaining == 0:
                return {"error": f"'{class_name}' is fully booked (capacity {cls.get('capacity')})."}
            return {"error": f"'{class_name}' only has {remaining} spot(s) left. Cannot book {num_spots}."}
        class_id = cls.get("class_id", "")
        doc.setdefault("class_id",      class_id)
        doc.setdefault("facility_id",   class_id)
        doc.setdefault("facility_name", f"Class: {class_name}")
        doc.setdefault("booking_type",  "class")
        doc.setdefault("amount",        cls.get("fees", 0))
        schedule = cls.get("schedule", {})
        days_str = ", ".join(schedule.get("days", []))
        time_str = schedule.get("time", "")
        if days_str and time_str:
            doc.setdefault("time_slot", f"{days_str} · {time_str}")
        return None  # no error

    def _enrich_facility_booking(self, doc: dict, facility_name: str) -> None:
        """Enrich a facility booking doc in-place with facility_id, booking_type, amount."""
        facility = self.db["facilities"].find_one({"name": facility_name})
        if facility:
            doc.setdefault("facility_id",  facility.get("facility_id", ""))
            doc.setdefault("booking_type", "facility")
            doc.setdefault("amount",       facility.get("rate_per_hour", 0))

    def _do_insert(self, args: dict) -> dict:
        col_name = args.get("collection")
        if col_name in _BLOCKED:
            return {"error": f"Insert not allowed for '{col_name}'"}
        if not col_name or col_name not in self.collections:
            return {"error": f"Collection '{col_name}' not available"}
        doc = dict(args.get("document") or {})

        # Extract num_spots before setdefault calls (same-day group/family bookings)
        num_spots = int(doc.pop("num_spots", 1) or 1)
        if num_spots < 1:
            num_spots = 1

        doc.setdefault("status",     "confirmed")
        doc.setdefault("created_at", datetime.now().isoformat())
        doc.setdefault("source",     "voice")

        # Capture original names before enrichment
        class_name    = doc.get("class_name")    if col_name == "bookings" else None
        facility_name = doc.get("facility_name") if col_name == "bookings" else None

        # Auto-generate a unique booking ID for all bookings
        if col_name == "bookings":
            doc.setdefault("booking_id", f"CL{int(time.time() * 1000)}")

        # Validate + enrich booking document
        if class_name:
            err = self._enrich_class_booking(doc, class_name, num_spots)
            if err:
                return err
        elif facility_name:
            self._enrich_facility_booking(doc, facility_name)

        try:
            if num_spots > 1:
                # Same-day group/family booking — insert N copies with unique booking IDs
                base_ts = int(time.time() * 1000)
                for i in range(num_spots):
                    copy = dict(doc)
                    copy["booking_id"] = f"CL{base_ts}_{i}"
                    self.db[col_name].insert_one(copy)
                if class_name:
                    self.db["classes"].update_one({"name": class_name}, {"$inc": {"enrolled": num_spots}})
                self._invalidate_schema()
                return {"success": True, "collection": col_name, "num_inserted": num_spots}
            else:
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

            # Convert string _id to ObjectId so Gemini can pass query-result _id directly
            filter_ = dict(filter_)
            if "_id" in filter_:
                try:
                    filter_["_id"] = ObjectId(str(filter_["_id"]))
                except Exception:
                    pass

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
            # Convert string _id to ObjectId so Gemini can pass query-result _id directly
            filter_ = dict(filter_)
            if "_id" in filter_:
                try:
                    filter_["_id"] = ObjectId(str(filter_["_id"]))
                except Exception:
                    pass

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
