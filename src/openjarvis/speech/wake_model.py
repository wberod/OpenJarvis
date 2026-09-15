from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openjarvis.core.paths import get_data_dir


@dataclass(frozen=True)
class WakeModelAsset:
    name: str
    url: str
    sha256: str
    size: int


ASSETS = (
    WakeModelAsset(
        "hey_jarvis_v0.1.onnx",
        "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx",
        "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
        1271370,
    ),
    WakeModelAsset(
        "melspectrogram.onnx",
        "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx",
        "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
        1087958,
    ),
    WakeModelAsset(
        "embedding_model.onnx",
        "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx",
        "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
        1326578,
    ),
)


class WakeModelManager:
    def __init__(
        self, root: Path | None = None, model_name: str = "hey_jarvis_v0.1"
    ) -> None:
        self.root = root or get_data_dir() / "wake_models" / "openwakeword-v0.5.1"
        self.model_name = model_name

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _model_path(self) -> Path:
        return self.root / f"{self.model_name}.onnx"

    def installed(self) -> bool:
        """True when the configured wake-word model and base assets exist."""
        base_assets = list(ASSETS[1:]) if ASSETS else []
        return (
            self._model_path().is_file()
            and all(
                (path := self.root / asset.name).is_file()
                and path.stat().st_size == asset.size
                and self._digest(path) == asset.sha256
                for asset in base_assets
            )
        )

    @property
    def classifier_path(self) -> Path:
        """Path to the configured wake-word classifier."""
        return self._model_path()

    def install(
        self,
        *,
        consent: bool,
        opener: Callable[..., object] = urllib.request.urlopen,
    ) -> Path:
        if not consent:
            raise PermissionError("Wake model download requires explicit consent")
        expected_name = f"{self.model_name}.onnx"
        if not ASSETS or ASSETS[0].name != expected_name:
            raise RuntimeError(
                f"No downloadable openWakeWord model for '{self.model_name}'. "
                "Provide a trained .onnx file or use browser fallback."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        wake_asset, *base_assets = ASSETS
        for asset in [wake_asset, *base_assets]:
            destination = self.root / asset.name
            if destination.is_file() and self._digest(destination) == asset.sha256:
                continue
            temporary = destination.with_suffix(destination.suffix + ".part")
            try:
                response_context: Any = opener(asset.url, timeout=60)
                with response_context as response, temporary.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                valid_size = temporary.stat().st_size == asset.size
                valid_digest = self._digest(temporary) == asset.sha256
                if not valid_size or not valid_digest:
                    raise ValueError(f"Integrity check failed for {asset.name}")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        metadata = {
            "model": self.model_name,
            "version": "openWakeWord-v0.5.1",
            "source": ASSETS[0].url,
            "license": "CC BY-NC-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            "assets": [{"name": item.name, "sha256": item.sha256} for item in ASSETS],
        }
        temporary_metadata = self.root / "metadata.json.part"
        temporary_metadata.write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_metadata, self.root / "metadata.json")
        return self.classifier_path
