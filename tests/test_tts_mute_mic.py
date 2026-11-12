"""TTS_MUTE_MIC：默认关麦静音，避免播报期间丢掉下一句。"""

from __future__ import annotations

from app import config


def test_tts_mute_mic_default_off() -> None:
    assert config.TTS_MUTE_MIC is False
