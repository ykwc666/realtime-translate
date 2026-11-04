"""Clause-stream TTS: first-clause TTFA beats full-utterance synth on multi-clause text."""
from __future__ import annotations

from app import config
from app.tts_play import TtsPlayer, split_tts_clauses


def test_split_tts_clauses():
    parts = split_tts_clauses(
        "Hang on a second, I'll be right back after I check the notes."
    )
    assert len(parts) >= 2
    assert parts[0].endswith(",")


def test_clause_stream_ttfa_faster_than_batch():
    tts = TtsPlayer()
    tts.warmup()
    text = "Hang on a second, I'll be right back after I check the meeting notes."
    lang = "en"
    assert len(split_tts_clauses(text)) >= 2

    config.TTS_STREAM_PLAY = False
    batch = tts.synthesize(text, lang, save_wav=False)

    config.TTS_STREAM_PLAY = True
    stream, _play = tts.speak(text, lang, save_wav=False)

    assert batch.ms > 80
    assert stream.ttfa_ms > 0
    assert stream.ttfa_ms < batch.ms * 0.75, (
        f"clause ttfa {stream.ttfa_ms:.0f} not faster than batch {batch.ms:.0f}"
    )
    config.TTS_STREAM_PLAY = True
