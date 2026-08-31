"""Fish Audio TTS backend — cloud synthesis via the Fish Audio API."""

from __future__ import annotations

import os
from typing import List

import httpx

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

_FISH_AUDIO_TTS_URL = "https://api.fish.audio/v1/tts"


def _fish_audio_request(
    api_key: str,
    text: str,
    voice_id: str,
    model: str,
    output_format: str = "mp3",
) -> bytes:
    """Call the Fish Audio TTS API and return raw audio bytes."""
    resp = httpx.post(
        _FISH_AUDIO_TTS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "model": model,
        },
        json={
            "text": text,
            "reference_id": voice_id,
            "format": output_format,
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.content


@TTSRegistry.register("fish")
class FishAudioTTSBackend(TTSBackend):
    """Fish Audio TTS backend — cloud synthesis."""

    backend_id = "fish"

    def __init__(
        self,
        *,
        api_key: str = "",
        voice_id: str = "",
        model: str = "",
    ) -> None:
        self._api_key = api_key or os.environ.get("FISH_AUDIO_API_KEY", "")
        self._voice_id = voice_id or os.environ.get("FISH_AUDIO_VOICE_ID", "")
        self._model = model or os.environ.get("FISH_AUDIO_MODEL", "s2.1-pro-free")

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "mp3",
    ) -> TTSResult:
        if not self._api_key:
            raise RuntimeError("FISH_AUDIO_API_KEY not set")

        effective_voice_id = voice_id or self._voice_id
        if not effective_voice_id:
            raise RuntimeError(
                "FISH_AUDIO_VOICE_ID not set and no voice_id provided"
            )

        audio = _fish_audio_request(
            self._api_key,
            text,
            effective_voice_id,
            self._model,
            output_format,
        )

        return TTSResult(
            audio=audio,
            format=output_format,
            voice_id=effective_voice_id,
            metadata={"backend": "fish", "model": self._model},
        )

    def available_voices(self) -> List[str]:
        if not self._voice_id:
            return []
        return [self._voice_id]

    def health(self) -> bool:
        return bool(self._api_key)


__all__ = ["FishAudioTTSBackend"]
