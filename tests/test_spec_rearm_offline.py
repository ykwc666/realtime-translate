"""Re-arm offline-only: CUT merges online finalize + offline hint."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app import config
from app.asr_stream import AsrResult, prefer_2pass_asr
from app.dual_track import DualTrackEngine, DualTrackStats, SpecOfflineHint
from app.tts_queue import TtsQueue


class _SeqAsr:
    """Records call sequence; offline returns fixed text."""

    is_true_streaming = True

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.offline_text = "现在是星期啊"
        self.online_text = "现在是星期"
        self._2pass = True
        self.recognizer = object()

    def create_stream(self):  # noqa: ANN201
        return None

    def transcribe_with_2pass(self, audio: np.ndarray) -> AsrResult:
        self.calls.append(f"2pass:{int(np.asarray(audio).size)}")
        time.sleep(0.01)
        return AsrResult("full2pass", 20.0, int(np.asarray(audio).size))

    def transcribe_offline_only(self, audio: np.ndarray) -> AsrResult:
        self.calls.append(f"offline:{int(np.asarray(audio).size)}")
        time.sleep(0.01)
        return AsrResult(self.offline_text, 25.0, int(np.asarray(audio).size))

    def finalize_online_only(self, audio, state) -> AsrResult:  # noqa: ANN001
        self.calls.append("finalize_online")
        state.finished = True
        return AsrResult(self.online_text, 5.0, 100)

    def merge_online_with_offline_hint(
        self, online: AsrResult, offline_text: str, offline_ms: float
    ) -> AsrResult:
        self.calls.append("merge")
        merged = prefer_2pass_asr(online.text, offline_text)
        return AsrResult(merged, float(online.ms) + float(offline_ms), online.audio_samples)

    def finalize_stream(self, audio, state) -> AsrResult:  # noqa: ANN001
        self.calls.append("finalize_stream")
        state.finished = True
        return AsrResult(
            prefer_2pass_asr(self.online_text, self.offline_text),
            30.0,
            int(np.asarray(audio).size),
        )


class _FakeMt:
    def translate(self, text: str, **_k):  # noqa: ANN003
        return SimpleNamespace(text=f"T:{text}", ms=1.0)


def _shell(asr: _SeqAsr) -> DualTrackEngine:
    from app.asr_stream import AsrIncState
    from app.audio_buf import AudioBuffer
    from app.endpoint_arbiter import EndpointArbiter
    from app.stable_prefix import StablePrefixTracker

    eng = DualTrackEngine.__new__(DualTrackEngine)
    eng.asr = asr
    eng.translator = _FakeMt()
    eng.tts = None
    eng.play = False
    eng.force_src_lang = "zh"
    eng.label = ""
    eng.endpoint = SimpleNamespace(
        in_speech=True,
        trailing_silence_sec=0.0,
        feed=lambda _c: None,
        reset=lambda: None,
        reopen_after_hold=lambda: None,
    )
    eng._lock = threading.Lock()
    eng._partial_busy = False
    eng._pending_src = ""
    eng._pending_since = 0.0
    eng._translated_src = ""
    eng._utt_id = 1
    eng._asr_seq = 1
    eng._tts_seq = 0
    eng._tts_version = 0
    eng._asr_state = AsrIncState()
    eng.buf = AudioBuffer()
    eng.arbiter = EndpointArbiter()
    eng.tracker = StablePrefixTracker(agree_needed=1, min_commit_chars=1)
    eng.tts_q = TtsQueue(None, mute_cb=None, enabled=False)
    eng._utt_stats = {}
    eng._utt_speech_end = {}
    eng.spec_counters = {
        "starts": 0,
        "hits": 0,
        "cancel_speech": 0,
        "miss_grow": 0,
        "miss_timeout": 0,
        "skip_headstart": 0,
        "restarts": 0,
        "cpu_ms": 0.0,
    }
    eng.stats = DualTrackStats()
    eng.mt_ms_sum = 0.0
    eng.tts_ms_sum = 0.0
    eng.translated_bits = []
    eng.tgt_lang = "en"
    eng._last_partial_text = "现在是星"
    eng._unchanged_partials = 1
    eng._utt_announced = True
    eng._forced_truncation = False
    eng._holding_cut = False
    eng._asr_loop = False
    eng._cut_ready = None
    eng._spec = None
    eng._spec_starts_utt = 0
    eng._engine_silence_sec = 0.0
    eng.in_speech = True
    eng.audio_sec = 1.4
    eng.utt_t0 = time.perf_counter()
    eng.last_partial_t = eng.utt_t0
    eng.last_partial_audio_sec = 1.0
    eng._probe_snaps = {}
    eng._probe_reset = lambda: None  # type: ignore[method-assign]
    eng._t_speech_end = time.perf_counter()
    return eng


def _speech(n: int, rms: float = 0.2) -> np.ndarray:
    rng = np.random.default_rng(0)
    x = rng.standard_normal(n).astype(np.float32)
    x *= rms / (float(np.sqrt(np.mean(np.square(x)))) + 1e-9)
    return x


def _silence(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.float32)


@pytest.fixture
def restore_rearm_mode():
    old = config.ASR_SPECULATE_REARM_MODE
    yield
    config.ASR_SPECULATE_REARM_MODE = old


def test_offline_hit_merges_online_finalize(restore_rearm_mode):
    config.ASR_SPECULATE_REARM_MODE = "offline"
    asr = _SeqAsr()
    eng = _shell(asr)
    sr = int(config.SAMPLE_RATE)
    block = int(sr * 0.1)
    eng.buf.append(_speech(int(1.4 * sr)))
    # first full shot
    for _ in range(2):
        eng._note_chunk_energy(_silence(block))
    eng._maybe_speculate()
    assert eng._spec is not None
    assert eng._spec.get("kind") == "full"
    eng._spec["done"].wait(timeout=1.0)
    # cancel + offline re-arm
    eng._clear_speculate(reason="speech")
    eng.buf.append(_speech(block * 2))
    for _ in range(2):
        eng._note_chunk_energy(_silence(block))
        eng.buf.append(_silence(block))
    eng._maybe_speculate()
    assert eng._spec is not None
    assert eng._spec.get("kind") == "offline"
    eng._spec["done"].wait(timeout=1.0)
    audio = eng.buf.asarray()
    hint = eng._take_speculate(audio)
    assert isinstance(hint, SpecOfflineHint)
    online = asr.finalize_online_only(audio, eng._asr_state)
    final = asr.merge_online_with_offline_hint(
        online, hint.offline_text, hint.offline_ms
    )
    expected = prefer_2pass_asr(asr.online_text, asr.offline_text)
    assert final.text == expected
    assert "offline:" in ",".join(asr.calls)
    assert "finalize_online" in asr.calls
    assert "merge" in asr.calls
    assert "finalize_stream" not in asr.calls


def test_offline_incomplete_falls_back_finalize_stream(restore_rearm_mode):
    config.ASR_SPECULATE_REARM_MODE = "offline"
    asr = _SeqAsr()
    eng = _shell(asr)
    sr = int(config.SAMPLE_RATE)
    eng.buf.append(_speech(int(1.0 * sr)))
    eng._spec_starts_utt = 1  # force next as restart
    eng._engine_silence_sec = 0.25
    eng._maybe_speculate()
    job = eng._spec
    assert job is not None and job.get("kind") == "offline"
    # simulate CUT before done
    audio = eng.buf.asarray()
    # force not done
    job["done"].clear()
    # tiny budget by aging job into the future... use wait 0 by age large budget
    # Instead: leave done unset and monkeypatch wait to no-op timeout
    orig_wait = job["done"].wait

    def _instant_timeout(timeout=None):  # noqa: ANN001
        return False

    job["done"].wait = _instant_timeout  # type: ignore[method-assign]
    job["t0"] = time.perf_counter() - 10.0  # age past budget remain floor
    got = eng._take_speculate(audio)
    assert got is None
    assert eng.spec_counters["miss_timeout"] == 1
    # fallback path
    final = asr.finalize_stream(audio, eng._asr_state)
    assert "finalize_stream" in asr.calls
    assert final.text == prefer_2pass_asr(asr.online_text, asr.offline_text)
    job["done"].wait = orig_wait  # type: ignore[method-assign]


def test_mode_full_restart_uses_2pass(restore_rearm_mode):
    config.ASR_SPECULATE_REARM_MODE = "full"
    asr = _SeqAsr()
    eng = _shell(asr)
    sr = int(config.SAMPLE_RATE)
    eng.buf.append(_speech(int(1.0 * sr)))
    eng._spec_starts_utt = 1
    eng._engine_silence_sec = 0.25
    eng._maybe_speculate()
    assert eng._spec is not None
    assert eng._spec.get("kind") == "full"
    eng._spec["done"].wait(timeout=1.0)
    assert any(c.startswith("2pass:") for c in asr.calls)
    assert not any(c.startswith("offline:") for c in asr.calls)


def test_mode_off_skips_second_start(restore_rearm_mode):
    config.ASR_SPECULATE_REARM_MODE = "off"
    asr = _SeqAsr()
    eng = _shell(asr)
    sr = int(config.SAMPLE_RATE)
    eng.buf.append(_speech(int(1.0 * sr)))
    eng._spec_starts_utt = 1
    eng._engine_silence_sec = 0.25
    eng._maybe_speculate()
    assert eng._spec is None
    assert eng.spec_counters["starts"] == 0
    assert eng.spec_counters["restarts"] == 0
    assert asr.calls == []
