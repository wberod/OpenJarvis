from __future__ import annotations

import io
import logging
import math
import queue
import threading
import time
import wave
from array import array
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from openjarvis.speech.wake_model import WakeModelManager

logger = logging.getLogger(__name__)


class WakeDetector(Protocol):
    def predict(self, samples: array[int]) -> float: ...


class AudioSource(Protocol):
    def start(self, callback: Callable[[bytes], None]) -> None: ...
    def stop(self) -> None: ...


class OpenWakeWordDetector:
    def __init__(self, model_path: Path, model_root: Path | None = None) -> None:
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            raise RuntimeError("openwakeword is not installed") from exc
        root = model_root or model_path.parent
        self._model = Model(
            wakeword_models=[str(model_path)],
            melspec_model_path=str(root / "melspectrogram.onnx"),
            embedding_model_path=str(root / "embedding_model.onnx"),
            inference_framework="onnx",
        )

    def predict(self, samples: array[int]) -> float:
        import numpy as np

        predictions = self._model.predict(np.asarray(samples, dtype=np.int16))
        return max((float(value) for value in predictions.values()), default=0.0)


class SoundDeviceAudioSource:
    def __init__(
        self,
        sample_rate: int = 16000,
        block_size: int = 1280,
        device: str | int | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.device = device
        self._stream: Any = None
        self._lock = threading.Lock()

    def start(self, callback: Callable[[bytes], None]) -> None:
        with self._lock:
            if self._stream is not None:
                return
            try:
                import sounddevice
            except ImportError as exc:
                raise RuntimeError("sounddevice is not installed") from exc

            def receive(indata: Any, frames: int, timing: Any, status: Any) -> None:
                del frames, timing, status
                callback(bytes(indata))

            self._stream = sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                channels=1,
                dtype="int16",
                device=self.device,
                callback=receive,
            )
            self._stream.start()

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                logger.warning("Audio source stop failed: %s", exc)


class WakeWordService:
    def __init__(
        self,
        *,
        config: Any,
        speech_backend: Any,
        detector: WakeDetector | None = None,
        audio_source: AudioSource | None = None,
        model_manager: WakeModelManager | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.speech_backend = speech_backend
        self.model_manager = model_manager or WakeModelManager(
            model_name=config.model_id or "hey_jarvis_v0.1"
        )
        self.detector = detector
        self.audio_source = audio_source or SoundDeviceAudioSource(
            device=config.input_device or None,
        )
        self.sample_rate = getattr(self.audio_source, "sample_rate", 16000)
        self.block_size = getattr(self.audio_source, "block_size", 1280)
        self.clock = clock
        self.state = "disabled"
        self.error: str | None = None
        self.follow_up_until: float | None = None
        self._frames: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self._listeners: set[Callable[[dict[str, Any]], None]] = set()
        self._thread: threading.Thread | None = None
        self._loading_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._capture: list[bytes] = []
        self._speech_seen = False
        self._silence_started: float | None = None
        self._capture_started = 0.0
        self._last_wake = 0.0
        self._noise_floor = 0.001
        self._lock = threading.RLock()

    def subscribe(
        self,
        listener: Callable[[dict[str, Any]], None],
    ) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _emit(self, event_type: str, **payload: Any) -> None:
        event = {
            "type": event_type,
            "state": self.state,
            "timestamp": time.time(),
            **payload,
        }
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                pass

    def _set_state(self, state: str, error: str | None = None) -> None:
        with self._lock:
            self.state = state
            self.error = error
        self._emit("error" if error else "state_changed", error=error)

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "supported": self.model_manager.installed(),
            "enabled": self.state not in {"disabled", "error"},
            "model_installed": self.model_manager.installed(),
            "model_version": (
                "openWakeWord-v0.5.1" if self.model_manager.installed() else None
            ),
            "phrase": getattr(self.config, "phrase", "hey sc"),
            "detector_mode": "local",
            "input_device": getattr(self.audio_source, "device", None),
            "error": self.error,
            "follow_up_until": self.follow_up_until,
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if self._loading_thread and self._loading_thread.is_alive():
                return self.status()
            if not self.model_manager.installed() and self.detector is None:
                self._set_state("error", "Wake model is not installed")
                return self.status()
            if self.speech_backend is None:
                self._set_state("error", "Speech backend is not configured")
                return self.status()
            self._set_state("starting")
            self._stop.clear()
            # If the detector is already loaded (tests, fast restarts), keep the
            # start synchronous so callers immediately see "armed".
            if self.detector is not None:
                self._initialize()
                return self.status()
            # First load of the ONNX wake-word model can take several seconds,
            # so initialize in a background thread and emit state via WebSocket.
            self._loading_thread = threading.Thread(
                target=self._initialize,
                name="openjarvis-wake-init",
                daemon=True,
            )
            self._loading_thread.start()
            return self.status()

    def _initialize(self) -> None:
        try:
            if self.detector is None:
                logger.warning("Loading wake-word model...")
                self.detector = OpenWakeWordDetector(
                    self.model_manager.classifier_path,
                    self.model_manager.root,
                )
                logger.warning("Wake-word model loaded")
            if self._stop.is_set():
                return
            self.audio_source.start(self.feed_audio)
            if self._stop.is_set():
                self.audio_source.stop()
                return
            self._thread = threading.Thread(
                target=self._run,
                name="openjarvis-wake",
                daemon=True,
            )
            self._thread.start()
            self._drain_frames()
            self._set_state("armed")
        except Exception as exc:
            logger.exception("Wake-word initialization failed")
            self.audio_source.stop()
            msg = str(exc)
            if type(exc).__name__ == "PortAudioError":
                msg = (
                    "Microphone access denied or unavailable. "
                    "Grant Microphone permission to the terminal/app running the server "
                    "in System Settings → Privacy & Security → Microphone, then restart the server."
                )
            self._set_state("error", msg)

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self.audio_source.stop()
        loading = self._loading_thread
        if loading and loading is not threading.current_thread():
            loading.join(timeout=2)
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._loading_thread = None
        self._thread = None
        self._capture.clear()
        self._set_state("disabled")
        return self.status()

    def enter_follow_up(self) -> dict[str, Any]:
        if self.state in {"disabled", "error"}:
            return self.status()
        self.follow_up_until = self.clock() + float(self.config.follow_up_seconds)
        self._begin_capture("follow_up")
        return self.status()

    def _drain_frames(self) -> None:
        while not self._frames.empty():
            try:
                self._frames.get_nowait()
            except queue.Empty:
                break

    def feed_audio(self, frame: bytes) -> None:
        if self._stop.is_set() or not frame:
            return
        try:
            self._frames.put_nowait(frame)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                pass

    @staticmethod
    def _rms(samples: array[int]) -> float:
        if not samples:
            return 0.0
        mean_square = sum(value * value for value in samples) / len(samples)
        return math.sqrt(mean_square) / 32768.0

    def _voice_threshold(self) -> float:
        return max(0.003, min(0.03, self._noise_floor * 4))

    def _begin_capture(self, state: str = "capturing") -> None:
        self._capture = []
        self._speech_seen = False
        self._silence_started = None
        self._capture_started = self.clock()
        self._set_state(state)
        self._emit("capture_started")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                follow_up_expired = (
                    self.state == "follow_up"
                    and self.follow_up_until is not None
                    and self.clock() >= self.follow_up_until
                )
                if follow_up_expired:
                    self.follow_up_until = None
                    self._set_state("armed")
                continue
            samples = array("h")
            samples.frombytes(frame)
            now = self.clock()
            rms = self._rms(samples)
            if self.state == "armed":
                self._noise_floor = self._noise_floor * 0.995 + rms * 0.005
                if now - self._last_wake < float(self.config.cooldown_seconds):
                    continue
                try:
                    score = self.detector.predict(samples) if self.detector else 0.0
                except Exception as exc:
                    self._set_state("error", f"Wake detection failed: {exc}")
                    self._stop.set()
                    break
                if score >= 0.3 and now - getattr(self, "_last_score_log", 0) > 1.0:
                    logger.warning(
                        "Wake score %.3f (threshold %.3f)",
                        score,
                        float(self.config.threshold),
                    )
                    self._last_score_log = now
                if score >= float(self.config.threshold):
                    logger.warning(
                        "Wake word detected (score %.3f >= %.3f)",
                        score,
                        float(self.config.threshold),
                    )
                    self._last_wake = now
                    self._emit("wake_detected")
                    self._begin_capture()
                    self._capture.append(frame)
                    self._speech_seen = True
            elif self.state in {"capturing", "follow_up"}:
                follow_up_expired = (
                    self.state == "follow_up"
                    and not self._speech_seen
                    and self.follow_up_until is not None
                    and now >= self.follow_up_until
                )
                if follow_up_expired:
                    self.follow_up_until = None
                    self._set_state("armed")
                    continue
                if rms >= self._voice_threshold():
                    self._speech_seen = True
                    self._silence_started = None
                elif self._speech_seen and self._silence_started is None:
                    self._silence_started = now
                self._capture.append(frame)
                elapsed = now - self._capture_started
                silent = (
                    self._silence_started is not None
                    and now - self._silence_started
                    >= float(self.config.silence_seconds)
                )
                if silent or elapsed >= float(self.config.max_command_seconds):
                    self._transcribe()

    def _wav_bytes(self) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"".join(self._capture))
        return output.getvalue()

    def _transcribe(self) -> None:
        if not self._speech_seen:
            self._capture.clear()
            self._set_state("armed")
            return
        self._set_state("transcribing")
        self._emit("transcribing")
        try:
            wav_bytes = self._wav_bytes()
            pcm = b"".join(self._capture)
            duration = len(pcm) / (2 * self.sample_rate)
            zero_samples = sum(
                1
                for i in range(0, len(pcm), 2)
                if pcm[i:i + 2] == b"\x00\x00"
            )
            logger.debug(
                "Transcribing wake capture: %.2fs, %d frames, "
                "zero_samples=%d/%d",
                duration,
                len(self._capture),
                zero_samples,
                len(pcm) // 2,
            )
            if zero_samples > (len(pcm) // 2) * 0.9:
                logger.warning("Wake capture is mostly silence; skipping STT")
                self._last_wake = self.clock()
                self._emit("no_command", reason="mostly_silence")
                self._set_state("armed")
                return
            result = self.speech_backend.transcribe(
                wav_bytes,
                format="wav",
                language=None,
            )
            transcript = str(getattr(result, "text", "")).strip()
            logger.warning("Wake transcript: %r", transcript)
            if transcript:
                self._set_state("command_ready")
                self._emit("command_ready", transcript=transcript)
            else:
                logger.warning("Wake transcript empty; arming")
                self._last_wake = self.clock()
                self._emit("no_command", reason="empty_transcript")
            self._set_state("armed")
        except Exception as exc:
            logger.exception("Wake command transcription failed")
            self._set_state("error", f"Command transcription failed: {exc}")
        finally:
            self._capture.clear()
            self._drain_frames()
