"""Create a calendar event using the native Calendar app (macOS only for now)."""

from __future__ import annotations

import platform
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


_DEFAULT_CALENDAR: Optional[str] = None


def _default_calendar_name() -> str:
    """Return the best default Calendar app calendar name.

    Prefer the standard "Calendar" calendar if it exists; otherwise fall
    back to the first calendar. This avoids creating events in a stale or
    non-English calendar (e.g., "Casa") when the user actually uses the
    default "Calendar" calendar.
    """
    global _DEFAULT_CALENDAR
    if _DEFAULT_CALENDAR:
        return _DEFAULT_CALENDAR
    # Prefer "Calendar" if it exists.
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Calendar" to get name of (first calendar whose name is "Calendar")'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            name = result.stdout.strip()
            if name:
                _DEFAULT_CALENDAR = name
                return _DEFAULT_CALENDAR
    except Exception:
        pass
    # Fallback to the first calendar.
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Calendar" to get name of first calendar'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            name = result.stdout.strip()
            if name:
                _DEFAULT_CALENDAR = name
                return _DEFAULT_CALENDAR
    except Exception:
        pass
    return "Calendar"


def _normalize_ampm(text: str) -> str:
    """Normalize a.m./p.m. and Spanish am/pm markers."""
    text = text.lower()
    text = re.sub(r"a\.?m\.?", "am", text)
    text = re.sub(r"p\.?m\.?", "pm", text)
    text = text.replace("a.m", "am").replace("p.m", "pm")
    return text


def _parse_start_time(text: str) -> datetime:
    """Parse a free-form start time into a local datetime.

    Handles English and Spanish relative phrases like:
      - "today at 4pm", "tomorrow at 4:00 p.m."
      - "hoy a las 4pm", "mañana a las 4 de la tarde"
      - ISO-ish strings as a fallback
    """
    original = text.strip()
    text = _normalize_ampm(original)

    # Relative day offsets
    now = datetime.now()
    day_offset = 0
    if re.search(r"\btomorrow\b|\bmañana\b", text, re.IGNORECASE):
        day_offset = 1
    # Remove day words (keep 'hoy'/'today' as no-op)
    text = re.sub(r"\b(hoy|today|mañana|tomorrow)\b", "", text, flags=re.IGNORECASE)

    # Spanish "de la mañana/tarde/noche"
    text = re.sub(r"\bde\s+la\s+mañana\b", "am", text, flags=re.IGNORECASE)
    text = re.sub(r"\bde\s+la\s+tarde\b", "pm", text, flags=re.IGNORECASE)
    text = re.sub(r"\bde\s+la\s+noche\b", "pm", text, flags=re.IGNORECASE)
    text = re.sub(r"\ba\s+las\b", "", text, flags=re.IGNORECASE)

    # "mediodía" / "medianoche"
    text = re.sub(r"\bmediod[ií]a\b", "12:00 pm", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmedianoche\b", "12:00 am", text, flags=re.IGNORECASE)

    # Clean filler words
    text = text.replace("at", "").replace("en", "").replace("for", "")
    text = text.strip()

    # Try to extract hour/minute/period
    match = re.search(
        r"(\d{1,2})(?::(\d{2}))?(?:\s*(am|pm))?",
        text,
    )
    hour = None
    minute = 0
    period = None
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        period = match.group(3)

    if hour is None:
        # No recognizable time — fallback to dateutil if possible, otherwise now
        try:
            from dateutil import parser as date_parser

            parsed = date_parser.parse(text, fuzzy=True, default=now)
            parsed = parsed + timedelta(days=day_offset)
            return parsed.replace(second=0, microsecond=0)
        except Exception:
            return (now + timedelta(days=day_offset)).replace(second=0, microsecond=0)

    if period:
        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0

    base = (now + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return base


def _applescript_event_body(
    title: str,
    start: datetime,
    end: datetime,
    calendar_name: str,
    location: str,
    notes: str,
    recurrence: str = "",
) -> str:
    """Return the inner AppleScript body that creates one Calendar event."""
    # Escape double quotes in text fields so the AppleScript string doesn't break.
    title = title.replace('"', '\\"')
    location = location.replace('"', '\\"') if location else location
    notes = notes.replace('"', '\\"') if notes else notes
    recurrence = recurrence.replace('"', '\\"') if recurrence else recurrence
    return f"""    tell (first calendar whose name is "{calendar_name}")
        set startDate to current date
        set year of startDate to {start.year}
        set month of startDate to {start.month}
        set day of startDate to {start.day}
        set hours of startDate to {start.hour}
        set minutes of startDate to {start.minute}
        set seconds of startDate to 0
        set endDate to current date
        set year of endDate to {end.year}
        set month of endDate to {end.month}
        set day of endDate to {end.day}
        set hours of endDate to {end.hour}
        set minutes of endDate to {end.minute}
        set seconds of endDate to 0
        set props to {{summary:"{title}", start date:startDate, end date:endDate""" + (
        f', location:"{location}"' if location else ""
    ) + (
        f', description:"{notes}"' if notes else ""
    ) + (
        f', recurrence:"{recurrence}"' if recurrence else ""
    ) + """}
        make new event at end with properties props
    end tell"""


def _applescript_event(
    title: str,
    start: datetime,
    end: datetime,
    calendar_name: str,
    location: str,
    notes: str,
    recurrence: str = "",
) -> str:
    """Return a complete AppleScript that creates a single Calendar event."""
    body = _applescript_event_body(title, start, end, calendar_name, location, notes, recurrence)
    return f"""tell application "Calendar"
{body}
end tell"""


@ToolRegistry.register("create_calendar_event")
class CreateCalendarEventTool(BaseTool):
    """Create a new event in the default Calendar app."""

    tool_id = "create_calendar_event"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_calendar_event",
            description=(
                "Create a new calendar event in the user's default Calendar app. "
                "Use this whenever the user asks to schedule, add, or create a calendar "
                "event, reminder, or appointment. Ask for a title if it is missing. "
                "For start_time, provide a free-form phrase like 'today at 4pm', "
                "'tomorrow at 9am', 'hoy a las 4 de la tarde', or an ISO datetime. "
                "For a recurring daily reminder, pass repeat_for_days (e.g. 30) and a "
                "single start_time. The tool will create one event that repeats every day "
                "for that many days. Do not say the event was created unless this tool "
                "returns success. If you need more than one separate reminder, create one "
                "per tool call."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title for the calendar event.",
                    },
                    "start_time": {
                        "type": "string",
                        "description": (
                            "When the event starts. Free-form (e.g. 'today at 4pm', "
                            "'tomorrow at 9:00', 'hoy a las 16:00') or ISO 8601."
                        ),
                    },
                    "end_time": {
                        "type": "string",
                        "description": (
                            "Optional end time. Defaults to one hour after start_time."
                        ),
                    },
                    "calendar_name": {
                        "type": "string",
                        "description": "Name of the calendar to add the event to. Default: Calendar.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional location for the event.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes or description.",
                    },
                    "all_day": {
                        "type": "boolean",
                        "description": "Whether this is an all-day event.",
                    },
                    "repeat_for_days": {
                        "type": "integer",
                        "description": (
                            "Create a daily repeating event for this many days. "
                            "Example: 30 creates a reminder every day for 30 days."
                        ),
                    },
                    "recurrence": {
                        "type": "string",
                        "description": (
                            "Optional raw iCalendar recurrence rule. "
                            "Example: 'FREQ=DAILY;COUNT=30'. Use repeat_for_days for simple daily repeats."
                        ),
                    },
                },
                "required": ["title", "start_time"],
            },
            category="desktop",
            requires_confirmation=False,
            timeout_seconds=30.0,
            required_capabilities=[],
        )

    def execute(self, **params: Any) -> ToolResult:
        if platform.system() != "Darwin":
            return ToolResult(
                tool_name="create_calendar_event",
                content="Calendar event creation is only supported on macOS in this release.",
                success=False,
            )

        title = str(params.get("title") or "New event").strip()
        if not title:
            title = "New event"

        start_text = params.get("start_time")
        if not start_text:
            return ToolResult(
                tool_name="create_calendar_event",
                content="start_time is required.",
                success=False,
            )

        try:
            start = _parse_start_time(str(start_text))
        except Exception as exc:
            return ToolResult(
                tool_name="create_calendar_event",
                content=f"Could not understand the start time '{start_text}': {exc}",
                success=False,
            )

        end_text = params.get("end_time")
        if end_text:
            try:
                end = _parse_start_time(str(end_text))
            except Exception:
                end = start + timedelta(hours=1)
        else:
            end = start + timedelta(hours=1)

        calendar_name_raw = str(params.get("calendar_name") or "").strip()
        calendar_name = calendar_name_raw if calendar_name_raw else _default_calendar_name()
        location = str(params.get("location") or "")
        notes = str(params.get("notes") or "")
        all_day = bool(params.get("all_day", False))

        recurrence = ""
        repeat_for_days = params.get("repeat_for_days")
        if repeat_for_days is not None:
            try:
                days = int(repeat_for_days)
                if days > 0:
                    recurrence = f"FREQ=DAILY;COUNT={days}"
            except (ValueError, TypeError):
                pass
        if not recurrence:
            recurrence = str(params.get("recurrence") or "").strip()

        if all_day:
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

        script = _applescript_event(title, start, end, calendar_name, location, notes, recurrence)
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name="create_calendar_event",
                content="AppleScript timed out while creating the calendar event.",
                success=False,
            )
        except FileNotFoundError:
            return ToolResult(
                tool_name="create_calendar_event",
                content="osascript not found; calendar event creation requires macOS.",
                success=False,
            )

        if result.returncode != 0:
            return ToolResult(
                tool_name="create_calendar_event",
                content=f"Calendar error: {result.stderr.strip()}",
                success=False,
            )

        return ToolResult(
            tool_name="create_calendar_event",
            content=f"Created calendar event '{title}' at {start.strftime('%Y-%m-%d %H:%M')}.",
            success=True,
            metadata={
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "calendar": calendar_name,
            },
        )


@ToolRegistry.register("create_multiple_calendar_events")
class CreateMultipleCalendarEventsTool(BaseTool):
    """Create several Calendar events in one call — much faster for batch reminders."""

    tool_id = "create_multiple_calendar_events"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_multiple_calendar_events",
            description=(
                "Create multiple calendar events/reminders at once. Use this whenever the "
                "user asks for several reminders or events in a single request, for example "
                "'Create meal reminders for 5:30am, 8am, 12pm, 3pm and 7pm every day for 30 days'. "
                "Count the requested reminders carefully and create exactly that many events. "
                "Give each event a distinct title that reflects the reminder (e.g. 'Breakfast', "
                "'Lunch', 'Afternoon snack'). Each event can be a daily recurring series by "
                "setting repeat_for_days. Do not say the events were created unless this tool "
                "returns success."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "events": {
                        "type": "array",
                        "description": "List of events to create in one batch.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Distinct title for this specific reminder/event (e.g. 'Breakfast', 'Mid-morning snack', 'Dinner').",
                                },
                                "start_time": {
                                    "type": "string",
                                    "description": (
                                        "When the event starts. Free-form (e.g. 'today at 4pm', "
                                        "'tomorrow at 9:00') or ISO 8601."
                                    ),
                                },
                                "end_time": {
                                    "type": "string",
                                    "description": "Optional end time. Defaults to one hour after start_time.",
                                },
                                "calendar_name": {
                                    "type": "string",
                                    "description": "Calendar name. Defaults to the user's default Calendar calendar.",
                                },
                                "location": {
                                    "type": "string",
                                    "description": "Optional location.",
                                },
                                "notes": {
                                    "type": "string",
                                    "description": "Optional notes or description.",
                                },
                                "all_day": {
                                    "type": "boolean",
                                    "description": "Whether this is an all-day event.",
                                },
                                "repeat_for_days": {
                                    "type": "integer",
                                    "description": (
                                        "Create a daily repeating event for this many days. "
                                        "Example: 30 creates a reminder every day for 30 days."
                                    ),
                                },
                            },
                            "required": ["title", "start_time"],
                        },
                    },
                    "calendar_name": {
                        "type": "string",
                        "description": "Default calendar name for all events if not specified per event.",
                    },
                    "repeat_for_days": {
                        "type": "integer",
                        "description": "Default repeat count for all events if not specified per event.",
                    },
                },
                "required": ["events"],
            },
            category="desktop",
            requires_confirmation=False,
            timeout_seconds=60.0,
            required_capabilities=[],
        )

    def execute(self, **params: Any) -> ToolResult:
        if platform.system() != "Darwin":
            return ToolResult(
                tool_name="create_multiple_calendar_events",
                content="Calendar event creation is only supported on macOS in this release.",
                success=False,
            )

        raw_events = params.get("events")
        if not raw_events or not isinstance(raw_events, (list, tuple)):
            return ToolResult(
                tool_name="create_multiple_calendar_events",
                content="'events' must be a non-empty list of events to create.",
                success=False,
            )

        default_calendar_name = str(params.get("calendar_name") or "").strip() or _default_calendar_name()
        default_repeat = params.get("repeat_for_days")

        bodies: list[str] = []
        created: list[dict[str, Any]] = []

        for idx, raw in enumerate(raw_events):
            title = str(raw.get("title") or "New event").strip()
            if not title:
                title = "New event"

            start_text = raw.get("start_time")
            if not start_text:
                return ToolResult(
                    tool_name="create_multiple_calendar_events",
                    content=f"Event {idx + 1} is missing start_time.",
                    success=False,
                )

            try:
                start = _parse_start_time(str(start_text))
            except Exception as exc:
                return ToolResult(
                    tool_name="create_multiple_calendar_events",
                    content=f"Could not understand start_time for event {idx + 1} '{start_text}': {exc}",
                    success=False,
                )

            end_text = raw.get("end_time")
            if end_text:
                try:
                    end = _parse_start_time(str(end_text))
                except Exception:
                    end = start + timedelta(hours=1)
            else:
                end = start + timedelta(hours=1)

            calendar_name = str(raw.get("calendar_name") or "").strip() or default_calendar_name
            location = str(raw.get("location") or "")
            notes = str(raw.get("notes") or "")
            all_day = bool(raw.get("all_day", False))

            recurrence = ""
            repeat_for_days = raw.get("repeat_for_days")
            if repeat_for_days is None:
                repeat_for_days = default_repeat
            if repeat_for_days is not None:
                try:
                    days = int(repeat_for_days)
                    if days > 0:
                        recurrence = f"FREQ=DAILY;COUNT={days}"
                except (ValueError, TypeError):
                    pass
            if not recurrence:
                recurrence = str(raw.get("recurrence") or "").strip()

            if all_day:
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)

            bodies.append(
                _applescript_event_body(title, start, end, calendar_name, location, notes, recurrence)
            )
            created.append({"title": title, "start": start.isoformat(), "calendar": calendar_name})

        script = "tell application \"Calendar\"\n" + "\n".join(bodies) + "\nend tell"

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name="create_multiple_calendar_events",
                content="AppleScript timed out while creating the calendar events.",
                success=False,
            )
        except FileNotFoundError:
            return ToolResult(
                tool_name="create_multiple_calendar_events",
                content="osascript not found; calendar event creation requires macOS.",
                success=False,
            )

        if result.returncode != 0:
            return ToolResult(
                tool_name="create_multiple_calendar_events",
                content=f"Calendar error: {result.stderr.strip()}",
                success=False,
            )

        return ToolResult(
            tool_name="create_multiple_calendar_events",
            content=f"Created {len(created)} calendar event(s): {', '.join(c['title'] for c in created)}.",
            success=True,
            metadata={"events": created},
        )


__all__ = ["CreateCalendarEventTool", "CreateMultipleCalendarEventsTool"]
