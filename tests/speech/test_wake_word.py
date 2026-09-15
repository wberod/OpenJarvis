from __future__ import annotations

import time
from array import array
from types import SimpleNamespace

from openjarvis.speech._stubs import TranscriptionResult
from openjarvis.speech.wake_word import OpenWakeWordDetector, WakeWordService


class FakeDetector:
    def __init__(self):
        self.score = 0.0

    def predict(self, samples):
        return self.score


class FakeAudio:
    def __init__(self):
        self.callback = None
        self.running = False

    def start(self, callback):
        self.callback = callback
        self.running = True

    def stop(self):
        self.running = False


class FakeModels:
    root = None

    def installed(self):
        return True


class FakeSpeech:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return TranscriptionResult(
            text="turn on the lights",
            language="en",
            confidence=1.0,
            duration_seconds=1,
            segments=[],
        )


def config(**overrides):
    values = {
        "threshold": 0.5,
        "cooldown_seconds": 0.0,
        "silence_seconds": 0.01,
        "max_command_seconds": 1.0,
        "follow_up_seconds": 0.05,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def frame(value=0):
    return array("h", [value] * 1280).tobytes()


def wait_for(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_openwakeword_adapter_converts_samples_to_numpy():
    import numpy as np

    detector = OpenWakeWordDetector.__new__(OpenWakeWordDetector)

    class Model:
        def predict(self, samples):
            assert isinstance(samples, np.ndarray)
            assert samples.dtype == np.int16
            return {"hey_jarvis": 0.9}

    detector._model = Model()
    assert detector.predict(array("h", [1, 2, 3])) == 0.9


def test_start_stop_are_idempotent():
    audio = FakeAudio()
    service = WakeWordService(
        config=config(),
        speech_backend=FakeSpeech(),
        detector=FakeDetector(),
        audio_source=audio,
        model_manager=FakeModels(),
    )
    assert service.start()["state"] == "armed"
    assert service.start()["state"] == "armed"
    assert audio.running
    assert service.stop()["state"] == "disabled"
    assert service.stop()["state"] == "disabled"
    assert not audio.running


def test_wake_capture_transcribes_and_emits_command():
    detector = FakeDetector()
    detector.score = 1.0
    speech = FakeSpeech()
    service = WakeWordService(
        config=config(),
        speech_backend=speech,
        detector=detector,
        audio_source=FakeAudio(),
        model_manager=FakeModels(),
    )
    events = []
    service.subscribe(events.append)
    service.start()
    service.feed_audio(frame())
    wait_for(lambda: service.state == "capturing")
    detector.score = 0.0
    service.feed_audio(frame(8000))
    time.sleep(0.02)
    service.feed_audio(frame())
    time.sleep(0.02)
    service.feed_audio(frame())
    wait_for(lambda: any(event["type"] == "command_ready" for event in events))
    service.stop()
    assert len(speech.calls) == 1
    command = next(event for event in events if event["type"] == "command_ready")
    assert command["transcript"] == "turn on the lights"
    assert speech.calls[0][0].startswith(b"RIFF")


def test_follow_up_expires_without_audio():
    service = WakeWordService(
        config=config(),
        speech_backend=FakeSpeech(),
        detector=FakeDetector(),
        audio_source=FakeAudio(),
        model_manager=FakeModels(),
    )
    service.start()
    assert service.enter_follow_up()["state"] == "follow_up"
    wait_for(lambda: service.state == "armed")
    service.stop()
