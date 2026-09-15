"""Tests for the open_app tool."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.tools.open_app import OpenAppTool


def _register_import():
    """Force reimport of the tools package and verify registration."""
    import openjarvis.tools as tools_pkg
    from openjarvis.core.registry import ToolRegistry

    sys.modules.pop("openjarvis.tools.open_app", None)
    importlib.reload(tools_pkg)
    assert ToolRegistry.contains("open_app")


class TestOpenAppTool:
    def test_spec(self):
        tool = OpenAppTool()
        assert tool.spec.name == "open_app"
        assert tool.spec.category == "desktop"
        assert tool.spec.requires_confirmation is True
        assert "desktop:open" in tool.spec.required_capabilities

    def test_no_app_or_path(self):
        tool = OpenAppTool()
        result = tool.execute()
        assert result.success is False
        assert "Provide at least one" in result.content

    def test_missing_path(self):
        tool = OpenAppTool()
        result = tool.execute(path="/nonexistent/file.txt")
        assert result.success is False
        assert "Path not found" in result.content

    def test_rejects_shell_metacharacters(self):
        tool = OpenAppTool()
        result = tool.execute(app="calendar; rm -rf /")
        assert result.success is False
        assert "unsafe" in result.content.lower()

    def test_macos_opens_app_with_alias(self, tmp_path, monkeypatch):
        tool = OpenAppTool()
        monkeypatch.setattr("openjarvis.tools.open_app.platform.system", lambda: "Darwin")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        monkeypatch.setattr("openjarvis.tools.open_app.subprocess.run", fake_run)

        result = tool.execute(app="calendar")
        assert result.success is True
        assert calls == [["open", "-a", "Calendar.app"]]

    def test_macos_opens_file_with_app(self, tmp_path, monkeypatch):
        tool = OpenAppTool()
        test_file = tmp_path / "doc.txt"
        test_file.write_text("hello")
        monkeypatch.setattr("openjarvis.tools.open_app.platform.system", lambda: "Darwin")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        monkeypatch.setattr("openjarvis.tools.open_app.subprocess.run", fake_run)

        result = tool.execute(app="notes", path=str(test_file))
        assert result.success is True
        assert calls[0][:3] == ["open", "-a", "Notes.app"]
        assert calls[0][3].endswith("doc.txt")

    def test_linux_opens_file_default(self, tmp_path, monkeypatch):
        tool = OpenAppTool()
        test_file = tmp_path / "doc.txt"
        test_file.write_text("hello")
        monkeypatch.setattr("openjarvis.tools.open_app.platform.system", lambda: "Linux")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        monkeypatch.setattr("openjarvis.tools.open_app.subprocess.run", fake_run)

        result = tool.execute(path=str(test_file))
        assert result.success is True
        assert calls == [["xdg-open", str(test_file)]]

    def test_windows_opens_file_default(self, tmp_path, monkeypatch):
        tool = OpenAppTool()
        test_file = tmp_path / "doc.txt"
        test_file.write_text("hello")
        monkeypatch.setattr(
            "openjarvis.tools.open_app.platform.system", lambda: "Windows"
        )

        def fake_startfile(path):
            pass

        import os

        monkeypatch.setattr(os, "startfile", fake_startfile, raising=False)

        result = tool.execute(path=str(test_file))
        assert result.success is True
        assert "default application" in result.content

    def test_custom_aliases(self, tmp_path, monkeypatch):
        tool = OpenAppTool(
            custom_aliases={
                "myeditor": {"darwin": "MyEditor.app", "linux": "myeditor", "win32": "MyEditor.exe"},
            }
        )
        monkeypatch.setattr("openjarvis.tools.open_app.platform.system", lambda: "Darwin")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            return mock

        monkeypatch.setattr("openjarvis.tools.open_app.subprocess.run", fake_run)

        test_file = tmp_path / "a.txt"
        test_file.write_text("hello")
        result = tool.execute(app="myeditor", path=str(test_file))
        assert result.success is True
        assert "MyEditor.app" in calls[0]

    def test_failed_command(self, tmp_path, monkeypatch):
        tool = OpenAppTool()
        monkeypatch.setattr("openjarvis.tools.open_app.platform.system", lambda: "Darwin")

        def fake_run(cmd, **kwargs):
            mock = MagicMock()
            mock.returncode = 1
            mock.stdout = ""
            mock.stderr = "boom"
            return mock

        monkeypatch.setattr("openjarvis.tools.open_app.subprocess.run", fake_run)

        result = tool.execute(app="calendar")
        assert result.success is False
        assert "boom" in result.content
