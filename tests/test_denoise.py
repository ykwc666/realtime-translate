"""GTCRN MicDenoiser smoke (skip if model missing / disabled)."""
from __future__ import annotations

import numpy as np

from app import config
from app.denoise import MicDenoiser


def test_denoise_passthrough_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "MIC_DENOISE", False)
    d = MicDenoiser()
    assert not d.enabled
    x = np.random.randn(1600).astype(np.float32) * 0.05
    y = d.process(x)
    assert y.shape == x.shape
    assert np.allclose(x, y)


def test_denoise_online_when_enabled(monkeypatch):
    if not config.GTCRN_MODEL.is_file():
        return
    monkeypatch.setattr(config, "MIC_DENOISE", True)
    d = MicDenoiser()
    assert d.enabled
    fr = 256
    total = 0
    for _ in range(8):
        x = np.random.randn(fr).astype(np.float32) * 0.05
        y = d.process(x)
        total += int(y.size)
    # after first empty frame, should emit audio
    assert total >= fr * 6
