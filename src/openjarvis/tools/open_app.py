"""Open application tool — launch desktop apps and open files with the OS."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Maximum output size per stream (10 KB)
_MAX_OUTPUT_BYTES = 10_240

# Default timeout (seconds)
_DEFAULT_TIMEOUT = 30

# Common application aliases mapped to platform-specific identifiers.
# macOS uses bundle names ("Calendar.app"), Linux uses command names,
# Windows uses executable names.
_APP_ALIASES: Dict[str, Dict[str, str]] = {
    "calendar": {
        "darwin": "Calendar.app",
        "linux": "gnome-calendar",
        "win32": "outlook.exe",
    },
    "mail": {
        "darwin": "Mail.app",
        "linux": "thunderbird",
        "win32": "outlook.exe",
    },
    "email": {
        "darwin": "Mail.app",
        "linux": "thunderbird",
        "win32": "outlook.exe",
    },
    "notes": {
        "darwin": "Notes.app",
        "linux": "gedit",
        "win32": "notepad.exe",
    },
    "safari": {
        "darwin": "Safari.app",
        "linux": "firefox",
        "win32": "msedge.exe",
    },
    "browser": {
        "darwin": "Safari.app",
        "linux": "firefox",
        "win32": "msedge.exe",
    },
    "finder": {
        "darwin": "Finder.app",
        "linux": "nautilus",
        "win32": "explorer.exe",
    },
    "files": {
        "darwin": "Finder.app",
        "linux": "nautilus",
        "win32": "explorer.exe",
    },
    "reminders": {
        "darwin": "Reminders.app",
        "linux": "gnome-calendar",
        "win32": "outlook.exe",
    },
    "contacts": {
        "darwin": "Contacts.app",
        "linux": "thunderbird",
        "win32": "outlook.exe",
    },
    "maps": {
        "darwin": "Maps.app",
        "linux": "firefox",
        "win32": "msedge.exe",
    },
    "messages": {
        "darwin": "Messages.app",
        "linux": "thunderbird",
        "win32": "outlook.exe",
    },
    "music": {
        "darwin": "Music.app",
        "linux": "rhythmbox",
        "win32": "spotify.exe",
    },
    "photos": {
        "darwin": "Photos.app",
        "linux": "eog",
        "win32": "photos.exe",
    },
    "preview": {
        "darwin": "Preview.app",
        "linux": "eog",
        "win32": "photos.exe",
    },
    "terminal": {
        "darwin": "Terminal.app",
        "linux": "gnome-terminal",
        "win32": "cmd.exe",
    },
    "code": {
        "darwin": "Visual Studio Code.app",
        "linux": "code",
        "win32": "Code.exe",
    },
    "vscode": {
        "darwin": "Visual Studio Code.app",
        "linux": "code",
        "win32": "Code.exe",
    },
}


def _resolve_alias(name: str) -> str:
    """Resolve a friendly app name to the platform-specific identifier."""
    key = name.lower().strip()
    system = platform.system().lower()
    mapping = {
        "darwin": "darwin",
        "linux": "linux",
        "windows": "win32",
    }.get(system, system)
    if key in _APP_ALIASES and mapping in _APP_ALIASES[key]:
        return _APP_ALIASES[key][mapping]
    return name


def _has_shell_metacharacters(value: str) -> bool:
    """Return True if *value* contains dangerous shell metacharacters."""
    dangerous = set(";|&$`<>(){}[]\n\r\"")
    return any(ch in dangerous for ch in value)


def _validate_argument(value: Optional[str], label: str) -> Optional[str]:
    """Validate an argument is safe to pass to the OS launcher."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if _has_shell_metacharacters(value):
        raise ValueError(
            f"{label} contains unsafe shell characters: {value!r}"
        )
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


@ToolRegistry.register("open_app")
class OpenAppTool(BaseTool):
    """Open a desktop application or a file with its default application."""

    tool_id = "open_app"

    def __init__(
        self,
        custom_aliases: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> None:
        self._custom_aliases = custom_aliases or {}

    def _resolve_app(self, app: str) -> str:
        """Resolve app name through custom aliases, then built-in aliases."""
        key = app.lower().strip()
        system = platform.system().lower()
        mapping = {
            "darwin": "darwin",
            "linux": "linux",
            "windows": "win32",
        }.get(system, system)
        if key in self._custom_aliases and mapping in self._custom_aliases[key]:
            return self._custom_aliases[key][mapping]
        return _resolve_alias(app)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_app",
            description=(
                "Open a desktop application (e.g. Calendar, Mail) or open a "
                "file with its default application. On macOS this uses the "
                "`open` command; on Linux `xdg-open`; on Windows `start` / "
                "`os.startfile`. Provide either `app` or `path` (or both)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": (
                            "Name of the application to open. Common aliases: "
                            "calendar, mail, notes, safari, browser, finder, "
                            "files, reminders, contacts, messages, music, "
                            "photos, preview, terminal, code, vscode."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to a file or folder to open. If `app` is "
                            "also provided, the file is opened with that "
                            "application."
                        ),
                    },
                    "wait": {
                        "type": "boolean",
                        "description": (
                            "Wait for the application to exit before "
                            "returning. Default: false."
                        ),
                    },
                },
                "required": [],
            },
            category="desktop",
            requires_confirmation=False,
            timeout_seconds=60.0,
            required_capabilities=["desktop:open"],
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            app = _validate_argument(params.get("app"), "app")
            path = _validate_argument(params.get("path"), "path")
        except ValueError as exc:
            return ToolResult(
                tool_name="open_app",
                content=str(exc),
                success=False,
            )

        if not app and not path:
            return ToolResult(
                tool_name="open_app",
                content="Provide at least one of `app` or `path`.",
                success=False,
            )

        if path:
            path_obj = Path(path).expanduser()
            if not path_obj.exists():
                return ToolResult(
                    tool_name="open_app",
                    content=f"Path not found: {path}",
                    success=False,
                )
            path_str = str(path_obj)
        else:
            path_str = ""

        system = platform.system().lower()
        command_parts: List[str] = []
        shell = False

        try:
            if system == "darwin":
                command_parts = ["open"]
                if app:
                    command_parts.extend(["-a", self._resolve_app(app)])
                if path_str:
                    command_parts.append(path_str)

            elif system == "linux":
                if app:
                    resolved = self._resolve_app(app)
                    # Prefer the resolved executable if it exists on PATH.
                    if shutil.which(resolved):
                        command_parts = [resolved]
                    else:
                        command_parts = [resolved]
                    if path_str:
                        command_parts.append(path_str)
                else:
                    command_parts = ["xdg-open", path_str]

            elif system == "win32" or system.startswith("win"):
                if app and not path_str:
                    resolved = self._resolve_app(app)
                    if shutil.which(resolved):
                        command_parts = [resolved]
                    else:
                        # Fall back to start; uses shell=True but input is sanitized.
                        command_parts = ["start", "", resolved]
                        shell = True
                elif app and path_str:
                    resolved = self._resolve_app(app)
                    if shutil.which(resolved):
                        command_parts = [resolved, path_str]
                    else:
                        command_parts = ["start", "", resolved, path_str]
                        shell = True
                else:
                    import os

                    try:
                        os.startfile(path_str)
                        return ToolResult(
                            tool_name="open_app",
                            content=f"Opened {path_str} with the default application.",
                            success=True,
                            metadata={"command": f"os.startfile({path_str!r})"},
                        )
                    except OSError as exc:
                        return ToolResult(
                            tool_name="open_app",
                            content=f"Failed to open {path_str}: {exc}",
                            success=False,
                        )
            else:
                return ToolResult(
                    tool_name="open_app",
                    content=f"Unsupported platform: {system}",
                    success=False,
                )
        except ValueError as exc:
            return ToolResult(
                tool_name="open_app",
                content=f"Invalid argument: {exc}",
                success=False,
            )

        try:
            result = subprocess.run(
                command_parts,
                shell=shell,
                capture_output=True,
                text=True,
                timeout=_DEFAULT_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name="open_app",
                content=(
                    f"Timed out after {_DEFAULT_TIMEOUT}s while opening "
                    f"{app or path_str}."
                ),
                success=False,
            )
        except OSError as exc:
            return ToolResult(
                tool_name="open_app",
                content=f"Failed to launch: {exc}",
                success=False,
            )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if len(stdout) > _MAX_OUTPUT_BYTES:
            stdout = stdout[:_MAX_OUTPUT_BYTES] + "\n... (truncated)"
        if len(stderr) > _MAX_OUTPUT_BYTES:
            stderr = stderr[:_MAX_OUTPUT_BYTES] + "\n... (truncated)"

        sections: List[str] = []
        if stdout:
            sections.append(f"=== STDOUT ===\n{stdout}")
        if stderr:
            sections.append(f"=== STDERR ===\n{stderr}")

        description = " ".join(command_parts) if command_parts else path_str
        if result.returncode != 0:
            content = (
                f"Failed to open {app or path_str} (command: {description})."
            )
            if sections:
                content += "\n" + "\n".join(sections)
            return ToolResult(
                tool_name="open_app",
                content=content,
                success=False,
                metadata={
                    "command": description,
                    "returncode": result.returncode,
                },
            )

        content = f"Opened {app or path_str}"
        if app and path_str:
            content = f"Opened {path_str} with {app}"
        if sections:
            content += "\n" + "\n".join(sections)

        return ToolResult(
            tool_name="open_app",
            content=content,
            success=True,
            metadata={
                "command": description,
                "returncode": result.returncode,
            },
        )


__all__ = ["OpenAppTool"]
