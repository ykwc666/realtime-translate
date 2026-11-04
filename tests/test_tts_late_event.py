"""Late TTS play events still update DualTrackStats by utt_id after reset_utt."""
from __future__ import annotations

import time

from app.dual_track import DualTrackEngine, DualTrackStats
from app.tts_queue import TtsPlayEvent


class _DummyAsr:
    is_true_streaming = False

    def create_stream(self):  # noqa: ANN201
        return None


class _DummyMt:
    pass


def test_late_tts_event_fills_device_ttfa_after_reset():
    # Minimal engine shell — skip heavy model init by crafting object
    eng = DualTrackEngine.__new__(DualTrackEngine)
    eng._utt_stats = {}
    eng._utt_speech_end = {}
    eng._utt_id = 7
    eng._t_speech_end = time.perf_counter() - 0.5
    st = DualTrackStats(utt_id=7, t_mt_done_ms=200.0)
    eng._utt_stats[7] = st
    eng._utt_speech_end[7] = eng._t_speech_end
    # simulate reset creating a new current stats
    eng.stats = DualTrackStats(utt_id=8)

    t_cb = eng._t_speech_end + 0.35
    eng._on_tts_play_event(
        TtsPlayEvent(
            utt_id=7,
            seq=1,
            version=1,
            submit_t=eng._t_speech_end + 0.2,
            first_pcm_ready_t=eng._t_speech_end + 0.3,
            first_callback_t=t_cb,
            persistent=True,
            stream_reused=True,
            stream_open_ms=0.0,
            active_underruns=0,
            samples_expected=1000,
            samples_written=1000,
            interrupted=False,
            callback_valid=True,
        )
    )
    assert st.latency_device_ttfa_ms is not None
    assert 300 < st.latency_device_ttfa_ms < 400
    assert st.t_tts_pcm_ms is not None
    assert st.mt_ready_to_callback_ms is not None
    # must not have written onto the new utt stats
    assert eng.stats.latency_device_ttfa_ms is None


def test_interrupted_still_keeps_first_callback():
    eng = DualTrackEngine.__new__(DualTrackEngine)
    eng._utt_stats = {}
    eng._utt_speech_end = {}
    t_end = time.perf_counter() - 1.0
    st = DualTrackStats(utt_id=3, t_mt_done_ms=200.0)
    eng._utt_stats[3] = st
    eng._utt_speech_end[3] = t_end
    eng._on_tts_play_event(
        TtsPlayEvent(
            utt_id=3,
            seq=1,
            version=1,
            submit_t=t_end + 0.2,
            first_pcm_ready_t=t_end + 0.25,
            first_callback_t=t_end + 0.30,
            persistent=True,
            interrupted=True,
            callback_valid=True,
            samples_expected=8000,
            samples_written=1500,
        )
    )
    assert st.latency_device_ttfa_ms is not None
    assert abs(st.latency_device_ttfa_ms - 300.0) < 5.0
    assert st.tts_interrupted is True
