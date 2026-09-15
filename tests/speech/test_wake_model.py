from __future__ import annotations

import hashlib
import io

import pytest

from openjarvis.speech import wake_model
from openjarvis.speech.wake_model import WakeModelAsset, WakeModelManager


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def asset(name, content):
    return WakeModelAsset(
        name,
        f"https://example.test/{name}",
        hashlib.sha256(content).hexdigest(),
        len(content),
    )


def test_install_requires_consent(tmp_path):
    manager = WakeModelManager(tmp_path)
    with pytest.raises(PermissionError):
        manager.install(consent=False)


def test_install_verifies_and_atomically_records_metadata(tmp_path, monkeypatch):
    contents = {"hey.onnx": b"wake", "mel.onnx": b"mel", "embed.onnx": b"embed"}
    assets = tuple(asset(name, content) for name, content in contents.items())
    monkeypatch.setattr(wake_model, "ASSETS", assets)
    manager = WakeModelManager(tmp_path, model_name="hey")

    def opener(url, timeout):
        del timeout
        return Response(contents[url.rsplit("/", 1)[-1]])

    assert manager.install(consent=True, opener=opener) == tmp_path / "hey.onnx"
    assert manager.installed()
    assert (tmp_path / "metadata.json").is_file()
    assert not list(tmp_path.glob("*.part"))


def test_bad_checksum_removes_partial_download(tmp_path, monkeypatch):
    bad_asset = WakeModelAsset(
        "bad.onnx",
        "https://example.test/bad",
        "0" * 64,
        3,
    )
    monkeypatch.setattr(wake_model, "ASSETS", (bad_asset,))
    manager = WakeModelManager(tmp_path, model_name="bad")
    with pytest.raises(ValueError, match="Integrity check"):
        manager.install(consent=True, opener=lambda *args, **kwargs: Response(b"bad"))
    assert not (tmp_path / "bad.onnx").exists()
    assert not (tmp_path / "bad.onnx.part").exists()
