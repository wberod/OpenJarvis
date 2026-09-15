"""Tests for the server-side tool confirmation callback."""

from __future__ import annotations

import threading
import time

import pytest

from openjarvis.server.confirm_callback import ServerToolApprovalCallback
from openjarvis.tools.approval_store import ApprovalStore, STATUS_APPROVED, STATUS_DENIED


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Provide a callback using a temporary ApprovalStore."""
    db = tmp_path / "approvals.db"
    store = ApprovalStore(str(db))
    callback = ServerToolApprovalCallback(poll_interval=0.05)
    callback._store = store
    return callback, store


class TestServerToolApprovalCallback:
    def test_returns_false_when_denied(self, isolated_store):
        callback, store = isolated_store

        def deny_later():
            time.sleep(0.1)
            action = store.list_pending()[0]
            store.update_status(action.id, STATUS_DENIED)

        threading.Thread(target=deny_later, daemon=True).start()

        result = callback(
            "Allow execution of tool 'open_app' with args {'app': 'calendar'}?",
            {"tool": "open_app", "args": {"app": "calendar"}},
        )
        assert result is False

    def test_returns_true_when_approved(self, isolated_store):
        callback, store = isolated_store

        def approve_later():
            time.sleep(0.1)
            action = store.list_pending()[0]
            store.update_status(action.id, STATUS_APPROVED)

        threading.Thread(target=approve_later, daemon=True).start()

        result = callback(
            "Allow execution of tool 'open_app' with args {'app': 'calendar'}?",
            {"tool": "open_app", "args": {"app": "calendar"}},
        )
        assert result is True

    def test_queues_action_with_tool_call_type(self, isolated_store):
        callback, store = isolated_store

        def approve_later():
            time.sleep(0.05)
            action = store.list_pending()[0]
            store.update_status(action.id, STATUS_APPROVED)

        threading.Thread(target=approve_later, daemon=True).start()
        callback(
            "Allow execution of tool 'file_write' with args {'path': '/tmp/x.txt'}?",
            {"tool": "file_write", "args": {"path": "/tmp/x.txt"}},
        )

        pending = store.list_pending()
        assert len(pending) == 0  # approved actions are no longer pending
        actions = [
            a for a in store.list_pending.__self__._conn.execute(
                "SELECT action_type, description, payload FROM pending_actions"
            ).fetchall()
        ]
        assert actions[0][0] == "tool_call"
        assert "file_write" in actions[0][2]

    def test_timeout_returns_false(self, isolated_store):
        callback, store = isolated_store
        callback._timeout = 0.1

        result = callback(
            "Allow execution of tool 'open_app' with args {'app': 'calendar'}?",
            {"tool": "open_app", "args": {"app": "calendar"}},
        )
        assert result is False
