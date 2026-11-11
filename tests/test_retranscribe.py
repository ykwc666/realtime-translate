"""会后重译纯函数单测（无模型）。"""
from __future__ import annotations

import numpy as np

from app.retranscribe import (
    _alloc_timestamps,
    _alloc_timestamps_energy,
    split_chunks,
    split_sentences,
)


def test_split_chunks_alternating_speech_silence() -> None:
    sr = 16000
    target = 25.0
    # 130s：每 5s 一段语音(0.05) + 1s 静音，循环
    parts = []
    t = 0.0
    while t < 130.0:
        speech = np.full(int(5.0 * sr), 0.05, dtype=np.float32)
        silence = np.zeros(int(1.0 * sr), dtype=np.float32)
        parts.extend([speech, silence])
        t += 6.0
    audio = np.concatenate(parts)[: int(130 * sr)]
    chunks, _dropped = split_chunks(audio, sr=sr, target_sec=target, search_sec=3.0)
    assert len(chunks) >= 5
    max_len = int(1.2 * target * sr) + sr  # 容差
    for a, b in chunks:
        assert b - a <= max_len
        # 边界附近能量应低于块内中位（切在相对静音）
        if b < audio.size and b - a > sr:
            edge = audio[max(0, b - int(0.1 * sr)) : min(audio.size, b + int(0.1 * sr))]
            mid = audio[a + (b - a) // 3 : a + 2 * (b - a) // 3]
            if mid.size and edge.size:
                e_edge = float(np.sqrt(np.mean(np.square(edge))))
                e_mid = float(np.sqrt(np.mean(np.square(mid))))
                assert e_edge <= e_mid * 1.5 + 1e-4


def test_split_sentences_mixed_punct() -> None:
    parts = split_sentences("你好。How are you? 我很好！")
    assert len(parts) >= 2
    assert any("你好" in p for p in parts)


def test_split_sentences_decimal_and_abbrev() -> None:
    assert split_sentences("大概10.5秒") == ["大概10.5秒"]
    assert split_sentences("Mr. Smith") == ["Mr. Smith"]
    assert split_sentences("a.m.") == ["a.m."]


def test_alloc_timestamps_covers_span() -> None:
    sents = ["短", "这是长一点的句子内容"]
    stamped = _alloc_timestamps(sents, 1.0, 5.0)
    assert len(stamped) == 2
    assert abs(stamped[0][0] - 1.0) < 1e-6
    assert abs(stamped[-1][1] - 5.0) < 1e-6
    assert stamped[0][1] == stamped[1][0]


def test_alloc_timestamps_energy_cuts_in_silence() -> None:
    sr = 16000
    # 1.0s 语音 + 0.8s 静音 + 1.0s 语音；句界应落在静音窗
    speech_a = (0.05 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype(np.float32)
    silence = np.zeros(int(0.8 * sr), dtype=np.float32)
    speech_b = (0.05 * np.sin(2 * np.pi * 330 * np.arange(sr) / sr)).astype(np.float32)
    audio = np.concatenate([speech_a, silence, speech_b])
    t0, t1 = 0.0, audio.size / float(sr)
    stamped, mode = _alloc_timestamps_energy(
        ["第一句内容啊", "第二句内容啊"], audio, t0, t1, sr
    )
    assert len(stamped) == 2
    assert stamped[0][1] == stamped[1][0]
    cut = stamped[0][1]
    assert 1.0 - 0.15 <= cut <= 1.8 + 0.15
    assert mode in ("energy_split", "energy_split+approximate")
