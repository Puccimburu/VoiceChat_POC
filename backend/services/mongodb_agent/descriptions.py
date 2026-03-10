"""Text description builders for voice responses."""
from datetime import datetime


def build_confirm_summary(action_type: str, collection: str,
                           filter_: dict, updates: dict, document: dict) -> str:
    """Fallback summary when Gemini omits the summary arg."""
    combined   = {**(filter_ or {}), **(document or {})}
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
        booking_word = "" if subject.lower().endswith("booking") else " booking"
        parts = [f"I'd like to cancel{' ' + name + chr(39) + 's' if name else ''} {subject}{booking_word}"]
        if date: parts.append(f"on {date}")
        if slot: parts.append(f"at {slot}")
        return " ".join(parts) + ". Shall I proceed?"

    if action_type == "insert":
        parts = [f"I'd like to book {subject}{' for ' + name if name else ''}"]
        if date: parts.append(f"on {date}")
        if slot: parts.append(f"at {slot}")
        return " ".join(parts) + ". Shall I go ahead?"

    return f"I'm about to {action_type} on {collection}. Shall I proceed?"


def build_class_description(cls: dict) -> str:
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


def build_booking_description(bk: dict) -> str:
    """Synthesize a spoken summary of a booking when the model goes empty after
    a bookings query. Tells the user what booking was found and asks what they
    want to do with it."""
    member     = bk.get("member_name", "")
    class_name = bk.get("class_name", "")
    fac_name   = bk.get("facility_name", "") or ""
    # Strip the "Class: " prefix that is auto-added during insert enrichment
    if fac_name.startswith("Class: "):
        fac_name = ""
    subject   = class_name or fac_name or "booking"
    date_str  = bk.get("booking_date", "")
    time_slot = bk.get("time_slot", "")

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
