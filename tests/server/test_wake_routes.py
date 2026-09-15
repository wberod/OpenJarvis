from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server.wake_routes import create_wake_router
from openjarvis.speech.wake_word import WakeWordService


class Models:
    root = None

    def __init__(self):
        self.ready = True
        self.install = MagicMock()

    def installed(self):
        return self.ready


class Audio:
    def start(self, callback):
        self.callback = callback

    def stop(self):
        pass


class Detector:
    def predict(self, samples):
        return 0.0


def make_client():
    wake_config = SimpleNamespace(
        enabled=False,
        auto_start=False,
        threshold=0.5,
        cooldown_seconds=2.0,
        silence_seconds=1.5,
        max_command_seconds=30.0,
        follow_up_seconds=15.0,
        browser_fallback_consent=False,
    )
    models = Models()
    service = WakeWordService(
        config=wake_config,
        speech_backend=MagicMock(),
        detector=Detector(),
        audio_source=Audio(),
        model_manager=models,
    )
    app = FastAPI()
    app.state.api_key = ""
    app.include_router(create_wake_router(service))
    return TestClient(app), service, models


def test_status_start_stop_and_follow_up():
    client, service, _ = make_client()
    assert client.get("/v1/speech/wake/status").json()["state"] == "disabled"
    assert client.post("/v1/speech/wake/start").status_code == 200
    assert client.post("/v1/speech/wake/follow-up").json()["state"] == "follow_up"
    assert client.post("/v1/speech/wake/stop").json()["state"] == "disabled"
    service.stop()


def test_model_install_requires_consent():
    client, _, models = make_client()
    denied = client.post("/v1/speech/wake/model", json={"consent": False})
    accepted = client.post("/v1/speech/wake/model", json={"consent": True})
    assert denied.status_code == 400
    assert accepted.status_code == 200
    models.install.assert_called_once_with(consent=True)


def test_settings_are_validated_and_persisted():
    client, service, _ = make_client()
    with patch("openjarvis.server.wake_routes._persist_settings") as persist:
        response = client.patch(
            "/v1/speech/wake/settings",
            json={"threshold": 0.7, "auto_start": True},
        )
    assert response.status_code == 200
    assert service.config.threshold == 0.7
    persist.assert_called_once_with({"threshold": 0.7, "auto_start": True})
    invalid = client.patch(
        "/v1/speech/wake/settings",
        json={"threshold": 2},
    )
    assert invalid.status_code == 422


def test_websocket_receives_state_and_commands():
    client, service, _ = make_client()
    with client.websocket_connect("/v1/speech/wake/events") as websocket:
        assert websocket.receive_json()["type"] == "state_changed"
        service._emit("command_ready", transcript="hello")
        event = websocket.receive_json()
        assert event["type"] == "command_ready"
        assert event["transcript"] == "hello"
