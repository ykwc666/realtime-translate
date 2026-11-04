"""Speculate re-arm: speech resume clears stale job; second snap used at CUT."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np

from app import config
from app.dual_track import DualTrackEngine, DualTrackStats, SpecOfflineHint
from app.tts_queue import TtsQueue


class _FakeAsr:
    is_true_streaming = False

    def __init__(self) -> None:
        self.snap_lens: list[int] = []
        self._lock = threading.Lock()

    def create_stream(self):  # noqa: ANN201
        return None

    def finalize_stream(self, *_a, **_k):  # noqa: ANN003
        return SimpleNamespace(text="stream", ms=1.0, audio_samples=0)

    def transcribe_with_2pass(self, audio: np.ndarray):  # noqa: ANN201
        n = int(np.asarray(audio).size)
        with self._lock:
            self.snap_lens.append(n)
        # Finish quickly so CUT can hit
        time.sleep(0.02)
        return SimpleNamespace(text=f"snap{n}", ms=20.0, audio_samples=n)

    def transcribe_offline_only(self, audio: np.ndarray):  # noqa: ANN201
        return self.transcribe_with_2pass(audio)


class _FakeMt:
    def translate(self, text: str, **_k):  # noqa: ANN003
        return SimpleNamespace(text=f"T:{text}", ms=1.0)


def _shell(asr: _FakeAsr) -> DualTrackEngine:
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
    from app.asr_stream import AsrIncState
    from app.audio_buf import AudioBuffer
    from app.endpoint_arbiter import EndpointArbiter
    from app.stable_prefix import StablePrefixTracker

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
    return eng


def _speech(n: int, rms: float = 0.2) -> np.ndarray:
    # Deterministic non-silence chunk
    rng = np.random.default_rng(0)
    x = rng.standard_normal(n).astype(np.float32)
    x *= rms / (float(np.sqrt(np.mean(np.square(x)))) + 1e-9)
    return x


def _silence(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.float32)


def test_holding_cut_speech_clears_and_rearms():
    """speech → silence→spec → (holding) speech → silence → take second snap."""
    asr = _FakeAsr()
    eng = _shell(asr)
    sr = int(config.SAMPLE_RATE)
    block = int(sr * 0.1)

    # Seed buffer with ~1.4s speech so min_samples satisfied
    eng.buf.append(_speech(int(1.4 * sr)))
    eng.audio_sec = 1.4

    # First silence window → start spec
    eng._holding_cut = False
    for _ in range(2):
        eng._note_chunk_energy(_silence(block))
    assert eng._engine_silence_sec >= 0.08
    eng._maybe_speculate()
    assert eng._spec is not None
    n0 = int(eng._spec["n"])
    assert eng.spec_counters["starts"] == 1
    assert eng.spec_counters["restarts"] == 0
    # Let first job finish (will be abandoned)
    time.sleep(0.05)

    # Enter holding_cut; speech resumes — energy clock + maybe must cancel
    eng._holding_cut = True
    speech = _speech(block)
    eng.buf.append(speech)
    eng.audio_sec += speech.size / sr
    eng._note_chunk_energy(speech)
    eng._maybe_speculate()
    assert eng._spec is None
    assert eng.spec_counters["cancel_speech"] == 1
    assert eng._engine_silence_sec == 0.0

    # More speech then silence → re-arm with fuller buffer
    more = _speech(block * 2)
    eng.buf.append(more)
    eng.audio_sec += more.size / sr
    eng._note_chunk_energy(more)
    for _ in range(2):
        sil = _silence(block)
        eng.buf.append(sil)
        eng.audio_sec += sil.size / sr
        eng._note_chunk_energy(sil)
    eng._maybe_speculate()
    assert eng._spec is not None
    n1 = int(eng._spec["n"])
    assert n1 > n0
    assert eng.spec_counters["starts"] == 2
    assert eng.spec_counters["restarts"] == 1
    # Wait for second job
    eng._spec["done"].wait(timeout=1.0)
    assert eng._spec["done"].is_set()

    # CUT takes latest job (fuller snap), not miss_grow
    audio = eng.buf.asarray()
    got = eng._take_speculate(audio)
    assert got is not None
    # default REARM_MODE=offline → SpecOfflineHint; full mode → AsrResult-like
    if isinstance(got, SpecOfflineHint):
        assert got.offline_text
    assert eng.spec_counters["hits"] == 1
    assert eng.spec_counters["miss_grow"] == 0
    assert asr.snap_lens[-1] == n1
    assert n1 == int(audio.size) or n1 <= int(audio.size)


def test_max_starts_caps_rearms():
    asr = _FakeAsr()
    eng = _shell(asr)
    sr = int(config.SAMPLE_RATE)
    block = int(sr * 0.1)
    eng.buf.append(_speech(int(1.0 * sr)))
    eng.audio_sec = 1.0
    old_max = config.ASR_SPECULATE_MAX_STARTS
    try:
        config.ASR_SPECULATE_MAX_STARTS = 2
        for _cycle in range(3):
            eng._engine_silence_sec = 0.0
            eng._clear_speculate()
            for _ in range(2):
                eng._note_chunk_energy(_silence(block))
            eng._maybe_speculate()
        assert eng.spec_counters["starts"] == 2
        assert eng._spec_starts_utt == 2
        # Third attempt blocked
        eng._clear_speculate()
        eng._engine_silence_sec = 0.25
        eng._maybe_speculate()
        assert eng.spec_counters["starts"] == 2
        assert eng._spec is None
    finally:
        config.ASR_SPECULATE_MAX_STARTS = old_max
