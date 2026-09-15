"""Microsoft 365 connector — Outlook mail + calendar via Microsoft Graph.

Auth uses the shared OAuth 2.0 provider registry (``provider="microsoft"``,
see :mod:`openjarvis.connectors.oauth`): a plain auth-code flow against
``login.microsoftonline.com/{tenant}/oauth2/v2.0`` — the same flow used by
the Sheridan Calendar/BookMe app. Tokens are stored in
``~/.openjarvis/connectors/ms365.json`` and refreshed via the stored
``refresh_token``.

Setup: register an Entra app (multi-tenant or single-tenant), grant
``User.Read Mail.Read Calendars.Read`` delegated scopes, add the connector's
redirect URI, then set ``OPENJARVIS_MICROSOFT_CLIENT_ID`` /
``OPENJARVIS_MICROSOFT_CLIENT_SECRET`` (and optionally
``OPENJARVIS_MICROSOFT_TENANT_ID``, default ``common``).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.connectors.oauth import (
    delete_tokens,
    get_client_credentials,
    load_tokens,
    microsoft_tenant,
    provider_endpoint,
    save_tokens,
)
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DEFAULT_CREDENTIALS_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "ms365.json")

_MS_SCOPES = (
    "openid profile email offline_access User.Read Mail.Read"
    " Calendars.ReadWrite"
)

_CONNECTORS_DIR = DEFAULT_CONFIG_DIR / "connectors"


def credentials_path_for_user(user_id: str) -> str:
    """Return the per-user token file for *user_id*.

    The shared ``ms365.json`` holds the connector-level credential (used by
    background sync); per-user tokens from the OAuth ``state`` flow land in
    ``ms365-<user>.json`` so one person's grant is never used for another.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "", user_id)[:64]
    return str(_CONNECTORS_DIR / f"ms365-{safe}.json")


# ---------------------------------------------------------------------------
# Hub-backed per-user token store
#
# Microsoft sign-in is owned by the SC Hub (Command Center). The hub writes
# each user's Microsoft tokens into its Supabase profiles table; OpenJarvis
# reads them by the caller's email. Table/column names are configurable via
# env so they can match whatever the hub provisions.
# ---------------------------------------------------------------------------

_PROFILE_TABLE = os.environ.get("MS365_PROFILE_TABLE", "profiles")
_PROFILE_EMAIL_COL = os.environ.get("MS365_PROFILE_EMAIL_COL", "email")
_PROFILE_TOKEN_COL = os.environ.get("MS365_PROFILE_TOKEN_COL", "microsoft_access_token")
_PROFILE_REFRESH_COL = os.environ.get("MS365_PROFILE_REFRESH_COL", "microsoft_refresh_token")
_PROFILE_EXPIRY_COL = os.environ.get("MS365_PROFILE_EXPIRY_COL", "microsoft_token_expires_at")


def _hub_supabase() -> Optional[tuple]:
    """Return (base_url, key) for the hub Supabase project, or None."""
    url = (
        os.environ.get("SUPABASE_HUB_URL", "")
        or os.environ.get("HUB_SUPABASE_URL", "")
    ).rstrip("/")
    key = (
        os.environ.get("SUPABASE_HUB_KEY", "")
        or os.environ.get("HUB_SUPABASE_SERVICE_ROLE_KEY", "")
    )
    return (url, key) if url and key else None


def _is_email(user_id: str) -> bool:
    return "@" in (user_id or "")


def _load_user_tokens_supabase(email: str) -> Optional[Dict[str, Any]]:
    """Read a user's Microsoft tokens from the hub profiles table."""
    sb = _hub_supabase()
    if not sb:
        return None
    url, key = sb
    try:
        resp = httpx.get(
            f"{url}/rest/v1/{_PROFILE_TABLE}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            params={
                _PROFILE_EMAIL_COL: f"eq.{email}",
                "select": (
                    f"{_PROFILE_TOKEN_COL},{_PROFILE_REFRESH_COL},"
                    f"{_PROFILE_EXPIRY_COL}"
                ),
                "limit": 1,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        row = rows[0]
        return {
            "access_token": row.get(_PROFILE_TOKEN_COL),
            "refresh_token": row.get(_PROFILE_REFRESH_COL),
            "expires_at": row.get(_PROFILE_EXPIRY_COL),
        }
    except Exception as exc:
        logger.debug("Hub profiles token lookup failed: %s", exc)
        return None


def _save_user_tokens_supabase(email: str, tokens: Dict[str, Any]) -> bool:
    """Write refreshed Microsoft tokens back to the hub profiles table."""
    sb = _hub_supabase()
    if not sb:
        return False
    url, key = sb
    body: Dict[str, Any] = {}
    if tokens.get("access_token"):
        body[_PROFILE_TOKEN_COL] = tokens["access_token"]
    if tokens.get("refresh_token"):
        body[_PROFILE_REFRESH_COL] = tokens["refresh_token"]
    if tokens.get("expires_at") or tokens.get("expires_in"):
        if tokens.get("expires_at"):
            body[_PROFILE_EXPIRY_COL] = tokens["expires_at"]
        else:
            body[_PROFILE_EXPIRY_COL] = int(
                time.time() + int(tokens["expires_in"])
            )
    if not body:
        return False
    try:
        resp = httpx.patch(
            f"{url}/rest/v1/{_PROFILE_TABLE}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            params={_PROFILE_EMAIL_COL: f"eq.{email}"},
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.debug("Hub profiles token update failed: %s", exc)
        return False


def load_user_tokens(user_id: str) -> Optional[Dict[str, Any]]:
    """Load Microsoft tokens for *user_id*.

    Email identities resolve against the hub's Supabase profiles table;
    any other id uses the per-user local credential file.
    """
    if _is_email(user_id):
        return _load_user_tokens_supabase(user_id)
    path = credentials_path_for_user(user_id)
    return load_tokens(path)


def save_user_tokens(user_id: str, tokens: Dict[str, Any]) -> None:
    """Persist tokens for *user_id* (hub profiles table for emails)."""
    if _is_email(user_id):
        _save_user_tokens_supabase(user_id, tokens)
        return
    save_tokens(credentials_path_for_user(user_id), tokens)


def refresh_ms365_user_token(user_id: str) -> Optional[str]:
    """Refresh the stored Microsoft token for *user_id*; returns new token."""
    creds = load_user_tokens(user_id)
    if not creds:
        return None
    refresh_token = creds.get("refresh_token")
    provider = _microsoft_provider()
    client_creds = get_client_credentials(provider) if provider else None
    client_id = (client_creds or (None, None))[0]
    client_secret = (client_creds or (None, None))[1]
    tenant = microsoft_tenant()
    if not (refresh_token and client_id):
        return None
    resp = httpx.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": _MS_SCOPES,
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        return None
    tokens = resp.json()
    save_user_tokens(user_id, tokens)
    return tokens.get("access_token")


def call_as_user(
    api_fn: Callable[..., Dict[str, Any]],
    user_id: str,
    *args: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Call *api_fn* with the user's Microsoft access token.

    ``api_fn`` must accept ``(access_token, *args, **kwargs)``. Mirrors
    ``_call_with_refresh`` but resolves credentials per user (hub profiles
    table for emails, per-user file otherwise).
    """
    creds = load_user_tokens(user_id)
    token = (creds or {}).get("access_token") or (creds or {}).get("token")
    if not token:
        raise RuntimeError("no Microsoft token for this user")
    try:
        return api_fn(token, *args, **kwargs)
    except httpx.HTTPStatusError as exc:
        if exc.response is None or exc.response.status_code != 401:
            raise
    new_token = refresh_ms365_user_token(user_id)
    if not new_token:
        raise RuntimeError(
            "access token expired and refresh failed — the user must "
            "reconnect Microsoft in the SC Hub"
        )
    return api_fn(new_token, *args, **kwargs)


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


def refresh_ms365_token(path: str) -> Optional[str]:
    """Refresh the stored Microsoft access token.

    Mirrors :func:`openjarvis.connectors.oauth.refresh_google_token` but
    targets the Microsoft identity platform token endpoint (tenant-aware).
    Returns the new access token, or ``None`` when refresh is impossible.
    """
    tokens = load_tokens(path)
    if not tokens:
        return None
    refresh_token = tokens.get("refresh_token")
    client_id = tokens.get("client_id")
    client_secret = tokens.get("client_secret")
    if not (refresh_token and client_id and client_secret):
        return None

    provider = _microsoft_provider()
    if provider is None:
        return None

    try:
        resp = httpx.post(
            provider_endpoint(provider.token_endpoint),
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": _MS_SCOPES,
            },
            timeout=15.0,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None

    body = resp.json()
    new_access = body.get("access_token")
    if not new_access:
        return None

    tokens.update(
        {
            "access_token": new_access,
            "token": new_access,
            "token_type": body.get("token_type", "Bearer"),
            "expires_in": body.get("expires_in", 3600),
        }
    )
    if body.get("refresh_token"):
        tokens["refresh_token"] = body["refresh_token"]
    save_tokens(path, tokens)
    return new_access


def _microsoft_provider():
    from openjarvis.connectors.oauth import OAUTH_PROVIDERS

    return OAUTH_PROVIDERS.get("microsoft")


def _call_with_refresh(
    api_fn: Callable[..., Dict[str, Any]],
    credentials_path: str,
    *args: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Call a Graph API function, refreshing the token once on 401."""
    tokens = load_tokens(credentials_path) or {}
    token = tokens.get("access_token") or tokens.get("token") or ""
    try:
        return api_fn(token, *args, **kwargs)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code != 401:
            raise
    new_token = refresh_ms365_token(credentials_path)
    if not new_token:
        raise RuntimeError(
            "Microsoft 365 access token expired and refresh failed. "
            "Reconnect the connector."
        )
    return api_fn(new_token, *args, **kwargs)


# ---------------------------------------------------------------------------
# Graph API helpers (module-level for easy patching in tests)
# ---------------------------------------------------------------------------


def _graph_get(
    token: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """GET a Graph API path (or a full @odata.nextLink URL)."""
    url = path if path.startswith("http") else f"{_GRAPH_BASE}{path}"
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _graph_user_email(token: str) -> str:
    try:
        data = _graph_get(
            token, "/me", params={"$select": "mail,userPrincipalName"}
        )
        return data.get("mail") or data.get("userPrincipalName", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Document formatting
# ---------------------------------------------------------------------------


def _format_message(msg: Dict[str, Any]) -> str:
    lines: List[str] = [f"Subject: {msg.get('subject', '(no subject)')}"]
    sender = (msg.get("from") or {}).get("emailAddress") or {}
    if sender.get("address"):
        lines.append(f"From: {sender.get('name', '')} <{sender['address']}>")
    to_list = [
        (r.get("emailAddress") or {}).get("address", "")
        for r in msg.get("toRecipients", [])
    ]
    to_list = [a for a in to_list if a]
    if to_list:
        lines.append(f"To: {', '.join(to_list)}")
    if msg.get("receivedDateTime"):
        lines.append(f"Received: {msg['receivedDateTime']}")
    body = (msg.get("body") or {}).get("content", "")
    if msg.get("bodyPreview") and not body:
        lines.append(f"Preview: {msg['bodyPreview']}")
    elif body:
        lines.append(f"Body: {body}")
    return "\n".join(lines)


def _format_event(evt: Dict[str, Any]) -> str:
    lines: List[str] = [f"Title: {evt.get('subject', '(no title)')}"]
    start = (evt.get("start") or {}).get("dateTime", "")
    end = (evt.get("end") or {}).get("dateTime", "")
    if start or end:
        lines.append(f"When: {start} – {end}")
    location = (evt.get("location") or {}).get("displayName", "")
    if location:
        lines.append(f"Location: {location}")
    organizer = (evt.get("organizer") or {}).get("emailAddress") or {}
    if organizer.get("address"):
        lines.append(f"Organizer: {organizer['address']}")
    attendees = [
        (a.get("emailAddress") or {}).get("address", "")
        for a in evt.get("attendees", [])
    ]
    attendees = [a for a in attendees if a]
    if attendees:
        lines.append(f"Attendees: {', '.join(attendees)}")
    body = (evt.get("body") or {}).get("content", "")
    if body:
        lines.append(f"Description: {body}")
    return "\n".join(lines)


def _parse_graph_ts(value: str) -> datetime:
    if not value:
        return datetime.now()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.now()


# ---------------------------------------------------------------------------
# MS365Connector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("ms365")
class MS365Connector(BaseConnector):
    """Sync Outlook mail and calendar events from Microsoft Graph.

    Parameters
    ----------
    credentials_path:
        JSON file holding OAuth tokens. Defaults to
        ``~/.openjarvis/connectors/ms365.json``.
    max_messages:
        Maximum number of emails to pull per sync.
    """

    connector_id = "ms365"
    display_name = "Microsoft 365 (Outlook Mail + Calendar)"
    auth_type = "oauth"

    def __init__(
        self,
        credentials_path: str = "",
        *,
        max_messages: int = 200,
        calendar_days_ahead: int = 30,
    ) -> None:
        self._credentials_path = credentials_path or _DEFAULT_CREDENTIALS_PATH
        self._max_messages = max_messages
        self._calendar_days_ahead = calendar_days_ahead
        self._items_synced = 0
        self._last_sync: Optional[datetime] = None
        self._last_cursor: Optional[str] = None

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        tokens = load_tokens(self._credentials_path)
        if not tokens:
            return False
        return bool(tokens.get("access_token") or tokens.get("token"))

    def disconnect(self) -> None:
        delete_tokens(self._credentials_path)

    def auth_url(self) -> str:
        provider = _microsoft_provider()
        tokens = load_tokens(self._credentials_path)
        client_id = (tokens or {}).get("client_id", "") or os.environ.get(
            "OPENJARVIS_MICROSOFT_CLIENT_ID", ""
        )
        if not provider or not client_id:
            return (
                "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps"
                "/ApplicationsListBlade"
            )
        from urllib.parse import urlencode

        params = {
            "client_id": client_id,
            "response_type": "code",
            "response_mode": "query",
            "scope": _MS_SCOPES,
        }
        return f"{provider_endpoint(provider.auth_endpoint)}?{urlencode(params)}"

    def handle_callback(self, code: str) -> None:
        """Handle pasted credentials or a raw token.

        A ``client_id:client_secret`` pair persists the app registration so
        the in-process OAuth flow can run; anything else is stored as a token.
        """
        code = code.strip()
        if ":" in code and " " not in code and not code.startswith("ey"):
            client_id, client_secret = code.split(":", 1)
            existing = load_tokens(self._credentials_path) or {}
            existing["client_id"] = client_id.strip()
            existing["client_secret"] = client_secret.strip()
            save_tokens(self._credentials_path, existing)
        else:
            save_tokens(self._credentials_path, {"access_token": code})

    def sync(
        self,
        *,
        since: Optional[datetime] = None,
        cursor: Optional[str] = None,
    ) -> Iterator[Document]:
        """Yield mail and calendar Documents from Microsoft Graph."""
        if not self.is_connected():
            return

        self._items_synced = 0
        yield from self._sync_mail(since)
        yield from self._sync_calendar(since)
        self._last_sync = datetime.now()

    # ------------------------------------------------------------------
    # Mail + calendar sync
    # ------------------------------------------------------------------

    def _sync_mail(self, since: Optional[datetime]) -> Iterator[Document]:
        params: Dict[str, Any] = {
            "$top": 50,
            "$orderby": "receivedDateTime desc",
            "$select": (
                "id,subject,from,toRecipients,receivedDateTime,"
                "bodyPreview,body,webLink,conversationId"
            ),
        }
        if since is not None:
            since_str = since.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            params["$filter"] = f"receivedDateTime ge {since_str}"

        next_link: Optional[str] = "/me/messages"
        fetched = 0
        while next_link and fetched < self._max_messages:
            try:
                resp = _call_with_refresh(
                    _graph_get,
                    self._credentials_path,
                    next_link,
                    params=params if next_link == "/me/messages" else None,
                )
            except (httpx.HTTPStatusError, RuntimeError) as exc:
                logger.warning("MS365 mail sync failed: %s", exc)
                break

            for msg in resp.get("value", []):
                msg_id = msg.get("id", "")
                if not msg_id:
                    continue
                sender = (msg.get("from") or {}).get("emailAddress") or {}
                participants = [
                    (r.get("emailAddress") or {}).get("address", "")
                    for r in msg.get("toRecipients", [])
                ]
                participants = [p for p in participants if p]
                yield Document(
                    doc_id=f"ms365:mail:{msg_id}",
                    source="ms365",
                    doc_type="email",
                    content=_format_message(msg),
                    title=msg.get("subject", "(no subject)"),
                    author=sender.get("address", ""),
                    participants=participants,
                    timestamp=_parse_graph_ts(msg.get("receivedDateTime", "")),
                    url=msg.get("webLink"),
                    metadata={
                        "conversation_id": msg.get("conversationId", ""),
                    },
                )
                fetched += 1
                self._items_synced += 1
                if fetched >= self._max_messages:
                    break

            next_link = resp.get("@odata.nextLink")

    def _sync_calendar(self, since: Optional[datetime]) -> Iterator[Document]:
        now = datetime.now(timezone.utc)
        start = (since or (now - timedelta(days=7))).astimezone(timezone.utc)
        end = now + timedelta(days=self._calendar_days_ahead)
        params: Dict[str, Any] = {
            "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$top": 100,
            "$orderby": "start/dateTime",
            "$select": (
                "id,subject,start,end,location,organizer,attendees,"
                "body,webLink,isAllDay"
            ),
        }

        next_link: Optional[str] = "/me/calendarView"
        while next_link:
            try:
                resp = _call_with_refresh(
                    _graph_get,
                    self._credentials_path,
                    next_link,
                    params=params if next_link == "/me/calendarView" else None,
                )
            except (httpx.HTTPStatusError, RuntimeError) as exc:
                logger.warning("MS365 calendar sync failed: %s", exc)
                break

            for evt in resp.get("value", []):
                evt_id = evt.get("id", "")
                if not evt_id:
                    continue
                organizer = (evt.get("organizer") or {}).get(
                    "emailAddress"
                ) or {}
                participants = [
                    (a.get("emailAddress") or {}).get("address", "")
                    for a in evt.get("attendees", [])
                ]
                participants = [p for p in participants if p]
                yield Document(
                    doc_id=f"ms365:cal:{evt_id}",
                    source="ms365",
                    doc_type="event",
                    content=_format_event(evt),
                    title=evt.get("subject", "(no title)"),
                    author=organizer.get("address", ""),
                    participants=participants,
                    timestamp=_parse_graph_ts(
                        (evt.get("start") or {}).get("dateTime", "")
                    ),
                    url=evt.get("webLink"),
                    metadata={"is_all_day": evt.get("isAllDay", False)},
                )
                self._items_synced += 1

            next_link = resp.get("@odata.nextLink")

    def sync_status(self) -> SyncStatus:
        return SyncStatus(
            state="idle",
            items_synced=self._items_synced,
            last_sync=self._last_sync,
            cursor=self._last_cursor,
        )
