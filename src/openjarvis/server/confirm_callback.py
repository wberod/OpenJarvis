"""Server-side tool confirmation callback.

Queues tool actions in the shared ``ApprovalStore`` and blocks until the user
approves or denies via the frontend approval UI.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from openjarvis.tools.approval_store import (
    TIER_MEDIUM,
    ApprovalStore,
    PendingAction,
)


class ServerToolApprovalCallback:
    """Confirmation callback for ToolExecutor that waits on frontend approval.

    Parameters
    ----------
    timeout_seconds:
        How long to wait for the user to approve/deny a tool call.
    poll_interval:
        How often to poll the approval store while waiting.
    """

    _store: Optional[ApprovalStore] = None

    def __init__(
        self,
        timeout_seconds: float = 60.0,
        poll_interval: float = 0.5,
    ) -> None:
        self._timeout = timeout_seconds
        self._poll_interval = poll_interval

    @property
    def store(self) -> ApprovalStore:
        if self._store is None:
            self._store = ApprovalStore()
        return self._store

    def __call__(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> bool:
        """Request approval for a tool call and block until decided."""
        tool_name = "tool_call"
        args: Dict[str, Any] = {}
        if context is not None:
            tool_name = context.get("tool", tool_name)
            args = context.get("args", args) or {}

        permission_key = self._permission_key(tool_name, args)
        store = self.store

        # Honour remembered decisions
        rule = store.get_permission(permission_key)
        if rule is not None:
            if rule.decision == "always_approve":
                return True
            if rule.decision == "always_deny":
                return False

        description = self._describe(tool_name, args) or prompt
        action = store.queue_action(
            action_type="tool_call",
            description=description,
            payload={"tool": tool_name, "args": args},
            permission_key=permission_key,
            tier=TIER_MEDIUM,
            ttl_hours=1,
        )

        return self._wait_for_decision(action, store)

    def _wait_for_decision(self, action: PendingAction, store: ApprovalStore) -> bool:
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            current = store.get_action(action.id)
            if current.status == "approved":
                return True
            if current.status == "denied":
                return False
            if current.status in ("expired",):
                return False
            time.sleep(self._poll_interval)

        # Timed out — mark expired
        store.update_status(action.id, "expired")
        return False

    @staticmethod
    def _permission_key(tool_name: str, args: Dict[str, Any]) -> str:
        # Fingerprint the action so repeated identical requests can be remembered.
        fingerprint_parts = [tool_name]
        for key in sorted(args.keys()):
            value = args[key]
            if isinstance(value, (str, int, float, bool)):
                fingerprint_parts.append(f"{key}:{value}")
            elif value is None:
                fingerprint_parts.append(f"{key}:")
        return f"tool:{':'.join(fingerprint_parts)}"

    @staticmethod
    def _describe(tool_name: str, args: Dict[str, Any]) -> str:
        if tool_name == "open_app":
            app = args.get("app")
            path = args.get("path")
            if app and path:
                return f"Open {path} with {app}"
            if app:
                return f"Open application: {app}"
            if path:
                return f"Open file/folder: {path}"
            return "Open an application or file"
        if tool_name == "file_write":
            path = args.get("path", "unknown")
            mode = args.get("mode", "write")
            return f"{mode.capitalize()} file: {path}"
        if tool_name == "file_read":
            return f"Read file: {args.get('path', 'unknown')}"
        if tool_name == "shell_exec":
            return f"Run shell command: {args.get('command', 'unknown')}"
        return f"Run tool '{tool_name}' with args {json.dumps(args)}"


__all__ = ["ServerToolApprovalCallback"]
