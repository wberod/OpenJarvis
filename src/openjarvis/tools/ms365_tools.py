"""Microsoft 365 live tools — Outlook calendar and mail via Microsoft Graph.

Unlike the ``ms365`` connector (which syncs documents into the knowledge
base), these tools hit the Graph API in real time: today's meetings, mail
search, and event creation. They share the OAuth credentials stored by the
``ms365`` connector at ``~/.openjarvis/connectors/ms365.json``.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import httpx

from openjarvis.connectors.ms365 import (
    _DEFAULT_CREDENTIALS_PATH,
    _GRAPH_BASE,
    _call_with_refresh,
    _format_event,
    _format_message,
    _graph_get,
)
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _ms_auth_start_url() -> Optional[str]:
    """Resolve the shared Sheridan Microsoft sign-in start URL.

    Email-based identities sign in through the SC Hub's ``/auth/microsoft``
    route by default, which stores Microsoft tokens in its Supabase profiles
    table.  This can be overridden via ``OPENJARVIS_MS_AUTH_URL``.
    """
    explicit = os.environ.get("OPENJARVIS_MS_AUTH_URL", "").rstrip("/")
    if explicit:
        return explicit

    hub = (
        os.environ.get("OPENJARVIS_HUB_URL", "").rstrip("/")
        or "https://hub.sheridanfunds.com"
    )
    return f"{hub}/auth/microsoft"


def _not_connected(user_id: Optional[str] = None) -> ToolResult:
    """Build the auth-required result, including a sign-in link when possible.

    Email identities sign in through the SC Hub (which owns the Microsoft
    login and stores tokens in its Supabase profiles table); other
    identities get the local OAuth consent flow at
    ``/v1/connectors/ms365/oauth/start``. When no Entra app credentials are
    configured at all, the message names the missing env vars instead of
    pointing at a link that would fail.
    """
    from openjarvis.connectors.oauth import (
        get_client_credentials,
        get_provider_for_connector,
    )

    provider = get_provider_for_connector("ms365")
    creds = get_client_credentials(provider) if provider else None

    if not creds:
        content = (
            "Microsoft 365 is not configured. An admin must set "
            "OPENJARVIS_MICROSOFT_CLIENT_ID and OPENJARVIS_MICROSOFT_CLIENT_SECRET "
            "(or the plain MICROSOFT_* equivalents)."
        )
        return ToolResult(
            tool_name="ms365", content=content, success=False,
            metadata={"auth_required": True, "configured": False},
        )

    # Email identities authenticate through the SC Hub.
    if user_id and "@" in user_id:
        sign_in_url = _ms_auth_start_url()
        return_url = os.environ.get("OPENJARVIS_PUBLIC_URL", "").rstrip("/")
        if sign_in_url and return_url:
            sign_in_url = f"{sign_in_url}?next={quote(return_url, safe='')}"
        hub_url = (
            os.environ.get("SUPABASE_HUB_URL", "")
            or os.environ.get("HUB_SUPABASE_URL", "")
        )
        hub_key = (
            os.environ.get("SUPABASE_HUB_KEY", "")
            or os.environ.get("HUB_SUPABASE_SERVICE_ROLE_KEY", "")
        )
        if not (hub_url and hub_key):
            content = (
                "OpenJarvis cannot read Microsoft tokens from the SC Hub "
                f"({sign_in_url}). Please set SUPABASE_HUB_URL/HUB_SUPABASE_URL "
                "and SUPABASE_HUB_KEY/HUB_SUPABASE_SERVICE_ROLE_KEY "
                "in the environment, then sign in at the hub."
            )
            return ToolResult(
                tool_name="ms365",
                content=content,
                success=False,
                metadata={
                    "auth_required": True,
                    "configured": True,
                    "sign_in_url": sign_in_url,
                    "hub_misconfigured": True,
                },
            )
        content = f"Microsoft 365 sign-in required: {sign_in_url}"
    else:
        # Non-email identity (dev / standalone): the OAuth consent flow is
        # served by the API server and files tokens per user via ``state``.
        base = os.environ.get("OPENJARVIS_PUBLIC_URL", "").rstrip("/")
        path = "/v1/connectors/ms365/oauth/start"
        if user_id:
            path += f"?user={user_id}"
        sign_in_url = f"{base}{path}" if base else path
        content = (
            f"Microsoft 365 sign-in required: {sign_in_url}"
        )
    return ToolResult(
        tool_name="ms365",
        content=content,
        success=False,
        metadata={
            "auth_required": True,
            "configured": True,
            "sign_in_url": sign_in_url,
        },
    )


class _MS365ToolBase(BaseTool):
    """Shared credential resolution for per-user MS365 tools.

    ``_user`` is injected by ``AgentExecutor`` from the request's
    ``user_id``. Email identities resolve tokens from the hub's Supabase
    profiles table; other ids use ``ms365-<user>.json``; when unset the tool
    falls back to the shared connector credential file.
    """

    _user: Optional[str] = None

    def __init__(self, credentials_path: str = "") -> None:
        self._base_credentials_path = (
            credentials_path or _DEFAULT_CREDENTIALS_PATH
        )

    def _load(self) -> Optional[Dict[str, Any]]:
        from openjarvis.connectors.ms365 import load_user_tokens
        from openjarvis.connectors.oauth import load_tokens

        if self._user:
            return load_user_tokens(self._user)
        return load_tokens(self._base_credentials_path)

    def _token_present(self) -> bool:
        creds = self._load()
        return bool(creds and (creds.get("access_token") or creds.get("token")))

    def _call(self, api_fn: Callable[..., Any], *args: Any, **kwargs: Any):
        from openjarvis.connectors.ms365 import call_as_user

        if self._user:
            return call_as_user(api_fn, self._user, *args, **kwargs)
        return _call_with_refresh(
            api_fn, self._base_credentials_path, *args, **kwargs
        )


@ToolRegistry.register("ms365_calendar_events")
class MS365CalendarEventsTool(_MS365ToolBase):
    """List Outlook calendar events for a date range (defaults to today)."""

    tool_id = "ms365_calendar_events"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ms365_calendar_events",
            description=(
                "List Microsoft 365 / Outlook calendar events for a date "
                "range. Defaults to today. Use for 'what's on my calendar', "
                "'my next meeting', or schedule questions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": (
                            "How many days ahead to list (default 0 = today "
                            "only; e.g. 7 for the coming week)."
                        ),
                    },
                    "start_date": {
                        "type": "string",
                        "description": (
                            "ISO date (YYYY-MM-DD) to start from. "
                            "Defaults to today."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max events to return. Default 25.",
                    },
                },
                "required": [],
            },
            category="productivity",
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        if not self._token_present():
            return _not_connected(self._user)

        start_str = str(params.get("start_date") or "").strip()
        if start_str:
            try:
                start = datetime.fromisoformat(start_str).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"Invalid start_date: {start_str!r}",
                    success=False,
                )
        else:
            now = datetime.now(timezone.utc)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        days_ahead = int(params.get("days_ahead") or 0)
        end = start + timedelta(days=days_ahead + 1)
        max_results = max(1, min(int(params.get("max_results") or 25), 100))

        try:
            resp = self._call(
                _graph_get,
                "/me/calendarView",
                params={
                    "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "$top": max_results,
                    "$orderby": "start/dateTime",
                    "$select": (
                        "id,subject,start,end,location,organizer,"
                        "attendees,isAllDay,webLink"
                    ),
                },
            )
        except RuntimeError as exc:
            if "refresh failed" in str(exc):
                return _not_connected(self._user)
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Calendar query failed: {exc}",
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Calendar query failed: {exc}",
                success=False,
            )

        events = resp.get("value", [])
        if not events:
            return ToolResult(
                tool_name=self.tool_id,
                content="No events found in that range.",
                success=True,
            )

        lines = [f"{len(events)} event(s):"]
        for evt in events:
            lines.append("---")
            lines.append(_format_event(evt))
        return ToolResult(
            tool_name=self.tool_id,
            content="\n".join(lines),
            success=True,
            metadata={"count": len(events)},
        )


@ToolRegistry.register("ms365_mail_search")
class MS365MailSearchTool(_MS365ToolBase):
    """Search Outlook mail in real time via Microsoft Graph."""

    tool_id = "ms365_mail_search"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ms365_mail_search",
            description=(
                "Search the user's Outlook mailbox in real time. Pass "
                "keywords in 'query' (Graph $search) — e.g. a sender name, "
                "subject term, or 'from:someone@company.com'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms (Graph $search syntax).",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max messages to return. Default 10.",
                    },
                },
                "required": ["query"],
            },
            category="productivity",
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query") or "").strip()
        if not query:
            return ToolResult(
                tool_name=self.tool_id,
                content="No query provided.",
                success=False,
            )
        if not self._token_present():
            return _not_connected(self._user)

        max_results = max(1, min(int(params.get("max_results") or 10), 50))

        def _search(token: str) -> Dict[str, Any]:
            resp = httpx.get(
                f"{_GRAPH_BASE}/me/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "ConsistencyLevel": "eventual",
                },
                params={
                    "$search": f'"{query}"',
                    "$top": max_results,
                    "$select": (
                        "id,subject,from,toRecipients,receivedDateTime,"
                        "bodyPreview,body,webLink"
                    ),
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

        try:
            resp = self._call(_search)
        except RuntimeError as exc:
            if "refresh failed" in str(exc):
                return _not_connected(self._user)
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Mail search failed: {exc}",
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Mail search failed: {exc}",
                success=False,
            )

        messages = resp.get("value", [])
        if not messages:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"No messages matched '{query}'.",
                success=True,
            )

        lines = [f"{len(messages)} message(s) for '{query}':"]
        for msg in messages:
            lines.append("---")
            lines.append(_format_message(msg))
        return ToolResult(
            tool_name=self.tool_id,
            content="\n".join(lines),
            success=True,
            metadata={"count": len(messages)},
        )


@ToolRegistry.register("ms365_create_event")
class MS365CreateEventTool(_MS365ToolBase):
    """Create an Outlook calendar event via Microsoft Graph."""

    tool_id = "ms365_create_event"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ms365_create_event",
            description=(
                "Create a Microsoft 365 / Outlook calendar event. Provide "
                "subject, ISO start/end datetimes, optional location, body, "
                "and attendee emails."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Event title.",
                    },
                    "start": {
                        "type": "string",
                        "description": (
                            "Start datetime, ISO 8601 (e.g. "
                            "'2026-09-09T14:00:00')."
                        ),
                    },
                    "end": {
                        "type": "string",
                        "description": "End datetime, ISO 8601.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional location or Teams link note.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Optional event description.",
                    },
                    "attendees": {
                        "type": "string",
                        "description": (
                            "Comma-separated attendee email addresses."
                        ),
                    },
                    "timezone": {
                        "type": "string",
                        "description": (
                            "IANA timezone for start/end (e.g. "
                            "'America/New_York'). Default 'UTC'."
                        ),
                    },
                },
                "required": ["subject", "start", "end"],
            },
            category="productivity",
            timeout_seconds=30.0,
            requires_confirmation=True,
        )

    def execute(self, **params: Any) -> ToolResult:
        subject = str(params.get("subject") or "").strip()
        start = str(params.get("start") or "").strip()
        end = str(params.get("end") or "").strip()
        if not (subject and start and end):
            return ToolResult(
                tool_name=self.tool_id,
                content="subject, start, and end are required.",
                success=False,
            )
        if not self._token_present():
            return _not_connected(self._user)

        tz = str(params.get("timezone") or "UTC")
        event: Dict[str, Any] = {
            "subject": subject,
            "start": {"dateTime": start, "timeZone": tz},
            "end": {"dateTime": end, "timeZone": tz},
        }
        if params.get("location"):
            event["location"] = {"displayName": str(params["location"])}
        if params.get("body"):
            event["body"] = {
                "contentType": "text",
                "content": str(params["body"]),
            }
        attendees_raw = str(params.get("attendees") or "").strip()
        if attendees_raw:
            event["attendees"] = [
                {
                    "emailAddress": {"address": addr.strip()},
                    "type": "required",
                }
                for addr in attendees_raw.split(",")
                if addr.strip()
            ]

        def _create(token: str) -> Dict[str, Any]:
            resp = httpx.post(
                f"{_GRAPH_BASE}/me/events",
                headers={"Authorization": f"Bearer {token}"},
                json=event,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

        try:
            created = self._call(_create)
        except RuntimeError as exc:
            if "refresh failed" in str(exc):
                return _not_connected(self._user)
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Failed to create event: {exc}",
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Failed to create event: {exc}",
                success=False,
            )

        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"Event created: {created.get('subject', subject)}\n"
                f"When: {(created.get('start') or {}).get('dateTime', start)}"
                f" – {(created.get('end') or {}).get('dateTime', end)}\n"
                f"Link: {created.get('webLink', '')}"
            ),
            success=True,
            metadata={"event_id": created.get("id", "")},
        )


__all__ = [
    "MS365CalendarEventsTool",
    "MS365CreateEventTool",
    "MS365MailSearchTool",
]
