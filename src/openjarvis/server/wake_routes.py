from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from openjarvis.core.paths import get_config_path
from openjarvis.server.auth_middleware import websocket_authorized
from openjarvis.speech.wake_word import WakeWordService


class WakeModelInstall(BaseModel):
    consent: bool


class WakeSettingsUpdate(BaseModel):
    enabled: bool | None = None
    auto_start: bool | None = None
    input_device: str | None = Field(default=None, max_length=200)
    threshold: float | None = Field(default=None, ge=0.1, le=0.95)
    cooldown_seconds: float | None = Field(default=None, ge=0.5, le=30)
    silence_seconds: float | None = Field(default=None, ge=0.3, le=10)
    max_command_seconds: float | None = Field(default=None, ge=2, le=120)
    follow_up_seconds: float | None = Field(default=None, ge=0, le=120)
    browser_fallback_consent: bool | None = None


def _persist_settings(update: dict[str, Any], path: Path | None = None) -> None:
    import tomlkit

    config_path = path or get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    document = (
        tomlkit.parse(config_path.read_text(encoding="utf-8"))
        if config_path.exists()
        else tomlkit.document()
    )
    if "speech" not in document:
        document["speech"] = tomlkit.table()
    speech = document["speech"]
    if "wake" not in speech:
        speech["wake"] = tomlkit.table()
    wake = speech["wake"]
    for key, value in update.items():
        wake[key] = value
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
    os.replace(temporary, config_path)


def create_wake_router(service: WakeWordService) -> APIRouter:
    router = APIRouter(prefix="/v1/speech/wake", tags=["speech"])

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return service.status()

    @router.post("/start")
    async def start() -> dict[str, Any]:
        result = await asyncio.to_thread(service.start)
        if result["state"] == "error":
            raise HTTPException(status_code=503, detail=result.get("error"))
        return result

    @router.post("/stop")
    async def stop() -> dict[str, Any]:
        return await asyncio.to_thread(service.stop)

    @router.post("/follow-up")
    async def follow_up() -> dict[str, Any]:
        return service.enter_follow_up()

    @router.patch("/settings")
    async def settings(update: WakeSettingsUpdate) -> dict[str, Any]:
        values = update.model_dump(exclude_none=True)
        for key, value in values.items():
            setattr(service.config, key, value)
        await asyncio.to_thread(_persist_settings, values)
        if update.enabled is False:
            return await asyncio.to_thread(service.stop)
        return service.status()

    @router.post("/model")
    async def install_model(request: WakeModelInstall) -> dict[str, Any]:
        if not request.consent:
            raise HTTPException(
                status_code=400,
                detail="Explicit model license consent is required",
            )
        try:
            await asyncio.to_thread(service.model_manager.install, consent=True)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Wake model installation failed: {exc}",
            ) from exc
        return service.status()

    @router.websocket("/events")
    async def events(websocket: WebSocket) -> None:
        expected_key = getattr(websocket.app.state, "api_key", "")
        if not websocket_authorized(websocket, expected_key):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=16)
        loop = asyncio.get_running_loop()

        def receive(event: dict[str, Any]) -> None:
            def enqueue() -> None:
                if event_queue.full():
                    try:
                        event_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                event_queue.put_nowait(event)

            loop.call_soon_threadsafe(enqueue)

        unsubscribe = service.subscribe(receive)
        try:
            await websocket.send_json({"type": "state_changed", **service.status()})
            while True:
                await websocket.send_json(await event_queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()

    return router
