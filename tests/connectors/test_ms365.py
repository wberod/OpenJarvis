"""Tests for MS365Connector — Outlook mail + calendar via Microsoft Graph.

All Graph API calls are mocked; no network access is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry

# ---------------------------------------------------------------------------
# Fake Graph payloads
# ---------------------------------------------------------------------------

_MESSAGES_RESPONSE = {
    "value": [
        {
            "id": "msg-1",
            "subject": "Q3 pipeline review",
            "from": {
                "emailAddress": {
                    "name": "Ana Lopez",
                    "address": "ana@example.com",
                }
            },
            "toRecipients": [
                {"emailAddress": {"address": "me@sheridanfunds.com"}}
            ],
            "receivedDateTime": "2026-09-08T14:00:00Z",
            "bodyPreview": "See attached",
            "body": {"contentType": "text", "content": "Full body"},
            "webLink": "https://outlook.office.com/mail/msg-1",
            "conversationId": "conv-1",
        }
    ]
}

_EVENTS_RESPONSE = {
    "value": [
        {
            "id": "evt-1",
            "subject": "Credit committee",
            "start": {"dateTime": "2026-09-08T15:00:00Z"},
            "end": {"dateTime": "2026-09-08T16:00:00Z"},
            "location": {"displayName": "Teams"},
            "organizer": {"emailAddress": {"address": "boss@example.com"}},
            "attendees": [
                {"emailAddress": {"address": "me@sheridanfunds.com"}}
            ],
            "body": {"contentType": "text", "content": "Agenda"},
            "webLink": "https://outlook.office.com/calendar/evt-1",
            "isAllDay": False,
        }
    ]
}


def _make_connector(tmp_path: Path):
    from openjarvis.connectors.ms365 import MS365Connector

    creds = tmp_path / "ms365.json"
    creds.write_text(json.dumps({"access_token": "tok"}))
    return MS365Connector(credentials_path=str(creds))


def test_registered() -> None:
    from openjarvis.connectors.ms365 import MS365Connector

    # The registry is cleared before each test by the autouse conftest
    # fixture, so re-register imperatively (same pattern as test_gcalendar).
    ConnectorRegistry.register_value("ms365", MS365Connector)
    assert ConnectorRegistry.contains("ms365")
    assert ConnectorRegistry.get("ms365").connector_id == "ms365"


def test_not_connected(tmp_path: Path) -> None:
    from openjarvis.connectors.ms365 import MS365Connector

    c = MS365Connector(credentials_path=str(tmp_path / "missing.json"))
    assert c.is_connected() is False
    assert list(c.sync()) == []


def test_sync_yields_mail_and_events(tmp_path: Path) -> None:
    connector = _make_connector(tmp_path)

    def fake_call(api_fn, path, *args, **kwargs):
        # Dispatch on the Graph path argument
        target = args[0] if args else ""
        if "messages" in target:
            return _MESSAGES_RESPONSE
        if "calendarView" in target:
            return _EVENTS_RESPONSE
        return {"value": []}

    with patch(
        "openjarvis.connectors.ms365._call_with_refresh",
        side_effect=fake_call,
    ):
        docs: List[Document] = list(connector.sync())

    assert len(docs) == 2
    mail = docs[0]
    assert mail.source == "ms365"
    assert mail.doc_type == "email"
    assert mail.doc_id == "ms365:mail:msg-1"
    assert "Q3 pipeline review" in mail.content
    evt = docs[1]
    assert evt.doc_type == "event"
    assert evt.doc_id == "ms365:cal:evt-1"
    assert "Credit committee" in evt.content
    assert connector.sync_status().items_synced == 2


def test_refresh_ms365_token(tmp_path: Path) -> None:
    from openjarvis.connectors import ms365

    creds = tmp_path / "ms365.json"
    creds.write_text(
        json.dumps(
            {
                "access_token": "old",
                "refresh_token": "rt",
                "client_id": "cid",
                "client_secret": "sec",
            }
        )
    )

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "access_token": "new-tok",
                "refresh_token": "new-rt",
                "expires_in": 3600,
            }

    with patch("httpx.post", return_value=_Resp()) as post:
        token = ms365.refresh_ms365_token(str(creds))

    assert token == "new-tok"
    saved = json.loads(creds.read_text())
    assert saved["access_token"] == "new-tok"
    assert saved["refresh_token"] == "new-rt"
    assert "login.microsoftonline.com" in post.call_args[0][0]


def test_refresh_returns_none_without_creds(tmp_path: Path) -> None:
    from openjarvis.connectors import ms365

    assert ms365.refresh_ms365_token(str(tmp_path / "nope.json")) is None


# ---------------------------------------------------------------------------
# Live tools
# ---------------------------------------------------------------------------


def test_ms365_tools_not_connected(tmp_path: Path) -> None:
    from openjarvis.tools.ms365_tools import (
        MS365CalendarEventsTool,
        MS365MailSearchTool,
    )

    missing = str(tmp_path / "none.json")
    res = MS365CalendarEventsTool(credentials_path=missing).execute()
    assert res.success is False
    assert res.metadata["auth_required"] is True
    res = MS365MailSearchTool(credentials_path=missing).execute(query="x")
    assert res.success is False
    assert res.metadata["auth_required"] is True


def test_ms365_tools_sign_in_link(tmp_path: Path, monkeypatch) -> None:
    """When app creds exist but no user has consented, return a sign-in URL."""
    from openjarvis.connectors import oauth
    from openjarvis.tools.ms365_tools import MS365CalendarEventsTool

    creds = tmp_path / "ms365.json"
    creds.write_text(
        json.dumps({"client_id": "cid", "client_secret": "sec"})
    )
    monkeypatch.setenv("OPENJARVIS_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setenv("OPENJARVIS_MICROSOFT_CLIENT_ID", "cid")
    monkeypatch.setenv("OPENJARVIS_MICROSOFT_CLIENT_SECRET", "sec")
    tool = MS365CalendarEventsTool(credentials_path=str(creds))
    res = tool.execute()
    assert res.success is False
    assert res.metadata.get("sign_in_url", "").endswith(
        "/v1/connectors/ms365/oauth/start"
    )


def test_ms365_tools_email_user_gets_hub_link(tmp_path, monkeypatch) -> None:
    """Email identities are directed to the SC Hub for Microsoft sign-in."""
    from openjarvis.tools.ms365_tools import MS365CalendarEventsTool

    monkeypatch.setenv("OPENJARVIS_MICROSOFT_CLIENT_ID", "cid")
    monkeypatch.setenv("OPENJARVIS_MICROSOFT_CLIENT_SECRET", "sec")
    monkeypatch.setenv("OPENJARVIS_HUB_URL", "https://hub.sheridanfunds.com")
    tool = MS365CalendarEventsTool(
        credentials_path=str(tmp_path / "shared.json")
    )
    tool._user = "eitan@sheridanfunds.com"
    res = tool.execute()
    assert res.success is False
    assert res.metadata["auth_required"] is True
    assert res.metadata["sign_in_url"] == "https://hub.sheridanfunds.com"
    assert "SC Hub" in res.content


def test_load_user_tokens_supabase(monkeypatch) -> None:
    """Email users resolve tokens from the hub Supabase profiles table."""
    from openjarvis.connectors import ms365

    monkeypatch.setenv("SUPABASE_HUB_URL", "https://hub.supabase.co")
    monkeypatch.setenv("SUPABASE_HUB_KEY", "svc")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [
                {
                    "microsoft_access_token": "tok-1",
                    "microsoft_refresh_token": "rt-1",
                    "microsoft_token_expires_at": 12345,
                }
            ]

    with patch("httpx.get", return_value=_Resp()) as get:
        tokens = ms365.load_user_tokens("eitan@sheridanfunds.com")

    assert tokens["access_token"] == "tok-1"
    assert tokens["refresh_token"] == "rt-1"
    call = get.call_args
    assert "hub.supabase.co/rest/v1/profiles" in call[0][0]
    assert call[1]["params"]["email"] == "eq.eitan@sheridanfunds.com"


def test_ms365_calendar_events_tool(tmp_path: Path) -> None:
    from openjarvis.tools import ms365_tools

    creds = tmp_path / "ms365.json"
    creds.write_text(json.dumps({"access_token": "tok"}))
    tool = ms365_tools.MS365CalendarEventsTool(credentials_path=str(creds))

    with patch.object(
        ms365_tools, "_call_with_refresh", return_value=_EVENTS_RESPONSE
    ):
        res = tool.execute()

    assert res.success is True
    assert "Credit committee" in res.content
    assert res.metadata["count"] == 1
