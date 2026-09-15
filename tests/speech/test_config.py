"""Tests for speech configuration."""

from openjarvis.core.config import JarvisConfig, SpeechConfig, load_config


def test_speech_config_defaults():
    cfg = SpeechConfig()
    assert cfg.backend == "auto"
    assert cfg.model == "base"
    assert cfg.language == ""
    assert cfg.device == "auto"
    assert cfg.compute_type == "float16"
    assert cfg.wake.enabled is False
    assert cfg.wake.phrase == "hey jarvis"
    assert cfg.wake.follow_up_seconds == 15.0


def test_wake_config_loads_from_nested_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[speech.wake]\nenabled = true\nthreshold = 0.7\n')
    cfg = load_config(path)
    assert cfg.speech.wake.enabled is True
    assert cfg.speech.wake.threshold == 0.7


def test_jarvis_config_has_speech():
    cfg = JarvisConfig()
    assert hasattr(cfg, "speech")
    assert isinstance(cfg.speech, SpeechConfig)
    assert cfg.speech.backend == "auto"


def test_jarvis_system_has_speech_backend():
    """JarvisSystem has a speech_backend attribute."""
    from openjarvis.system import JarvisSystem

    assert "speech_backend" in JarvisSystem.__dataclass_fields__
