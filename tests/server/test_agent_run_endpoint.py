"""Tests for the /v1/agent/run endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server.routes import router  # noqa: E402


class FakeAgent:
    agent_id = "fake_agent"
    accepts_tools = True

    def run(self, input: str, context=None):
        result = MagicMock()
        result.content = f"Echo: {input}"
        result.tool_results = []
        result.turns = 1
        result.metadata = {}
        return result


def test_agent_run_endpoint():
    app = FastAPI()
    app.include_router(router)
    app.state.engine = MagicMock()
    app.state.model = "test-model"
    app.state.agent = FakeAgent()
    app.state.bus = None
    app.state.memory_service = None

    client = TestClient(app)
    response = client.post(
        "/v1/agent/run",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "open Calendar"}],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "Echo: open Calendar"
    assert data["model"] == "test-model"
    assert data["turns"] == 1


def test_agent_run_no_agent_returns_503():
    app = FastAPI()
    app.include_router(router)
    app.state.engine = MagicMock()
    app.state.model = "test-model"
    app.state.agent = None

    client = TestClient(app)
    response = client.post(
        "/v1/agent/run",
        json={
            "messages": [{"role": "user", "content": "open Calendar"}],
        },
    )
    assert response.status_code == 503
