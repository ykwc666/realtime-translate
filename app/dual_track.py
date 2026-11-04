"""精简双轨：流式 ASR 只做字幕草稿；句尾整句 MT/TTS。中途默认不译。"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

from . import config
from .asr_stream import AsrIncState, StreamingAsr, prefer_final_asr
from .audio_buf import AudioBuffer
from .endpoint_arbiter import ArbiterAction, EndpointArbiter
from .mt_policy import (
    has_repeated_span,
    is_weekday_incomplete,
    should_skip_final_mt,
)
from .stable_prefix import StablePrefixTracker
from .translate import Translator, detect_lang
from .tts_play import TtsPlayer
from .tts_queue import TtsQueue
from .vad_stream import SpeechSegment, create_endpoint


@dataclass
class DualTrackStats:
    partial_count: int = 0
    mt_triggers: int = 0
    mt_waits: int = 0
    mt_reasons: list[str] = field(default_factory=list)
    asr_ms: list[float] = field(default_factory=list)
    asr_model_samples: list[int] = field(default_factory=list)
    mt_ms: list[float] = field(default_factory=list)
    tts_ms: list[float] = field(default_factory=list)
    first_partial_ms: float | None = None
    first_stable_mt_ms: float | None = None
    utt_wall_ms: float | None = None
    # Perceived latency (speech_end → …); dualsim wall is NOT this
    t_cut_ms: float | None = None
    t_asr_done_ms: float | None = None
    t_mt_done_ms: float | None = None
    t_first_audio_ms: float | None = None
    latency_perceived_ms: float | None = None
    speculate_hit: bool = False
    source: str = ""
    translated: str = ""
    partial_interval_used: list[float] = field(default_factory=list)


class DualTrackEngine:
    """流式 ASR（字幕草稿）+ 声学收句 + 句尾整句 MT/TTS。"""

    def __init__(
        self,
        asr: StreamingAsr,
        translator: Translator,
        tts: TtsPlayer | None,
        play: bool = True,
        force_src_lang: str | None = None,
        label: str = "",
    ) -> None:
        self.asr = asr
        self.translator = translator
        self.tts = tts
        self.play = play
        # Fixed direction for duplex (mic=zh, loopback=en); None → detect_lang
        self.force_src_lang = force_src_lang
        self.label = label or ""
        self.endpoint = create_endpoint()
        tag = f" {self.label}" if self.label else ""
        print(f"[vad{tag}] backend={type(self.endpoint).__name__}")
        dir_s = (
            f"{force_src_lang}→{'en' if force_src_lang == 'zh' else 'zh'}"
            if force_src_lang in ("zh", "en")
            else "auto"
        )
        print(f"[mode{tag}] final_only dir={dir_s} — 中途不译，句尾整句 MT")
        self.tracker = StablePrefixTracker(
            agree_needed=config.STABLE_AGREE,
            min_commit_chars=config.MIN_COMMIT_CHARS,
        )
        self._lock = threading.Lock()
        self._partial_busy = False
        self._last_partial_text = ""
        self._pending_src = ""
        self._pending_since = 0.0
        self._translated_src = ""
        self._utt_id = 0
        self._asr_seq = 0
        self._tts_seq = 0
        self._tts_version = 0
        self._asr_state = AsrIncState()
        self.buf = AudioBuffer()
        self.arbiter = EndpointArbiter()
        self.tts_q = TtsQueue(
            tts if play else None,
            mute_cb=None,
            enabled=bool(play and config.TTS_ASYNC and tts is not None),
        )
        self.reset_utt()

    def reset_utt(self) -> None:
        self.buf.clear()
        self.in_speech = False
        self.last_partial_t = 0.0
        self.last_partial_audio_sec = 0.0
        self.audio_sec = 0.0
        self.utt_t0 = 0.0
        self.mt_ms_sum = 0.0
        self.tts_ms_sum = 0.0
        self.translated_bits: list[str] = []
        self.tgt_lang = "en"
        self._last_partial_text = ""
        self._pending_src = ""
        self._pending_since = 0.0
        self._translated_src = ""
        self._asr_seq = 0
        self._tts_seq = 0
        self._tts_version = 0
        self._asr_state = AsrIncState()
        self.stats = DualTrackStats()
        self.tracker.reset()
        self._unchanged_partials = 0
        self._utt_announced = False
        self._forced_truncation = False
        self._holding_cut = False
        self._asr_loop = False
        self._cut_ready: SpeechSegment | None = None
        self._spec: dict | None = None
        self._engine_silence_sec = 0.0
        self.arbiter.reset()

    def _begin_utt(self) -> None:
        self.buf.clear()
        self.tracker.reset()
        self.mt_ms_sum = 0.0
        self.tts_ms_sum = 0.0
        self.translated_bits = []
        self._pending_src = ""
        self._pending_since = 0.0
        self._translated_src = ""
        self._last_partial_text = ""
        self._asr_state = AsrIncState()
        self._utt_id += 1
        self._asr_seq = 0
        self._tts_seq = 0
        self._tts_version = 0
        self._forced_truncation = False
        self._holding_cut = False
        self._asr_loop = False
        self._cut_ready = None
        self._spec = None
        self.arbiter.reset()
        self.tts_q.invalidate(self._utt_id, 0, min_version=0)
        self.stats = DualTrackStats()
        self.utt_t0 = time.perf_counter()
        self.last_partial_t = self.utt_t0
        self.last_partial_audio_sec = 0.0
        self.audio_sec = 0.0
        self.in_speech = True
        self._unchanged_partials = 0
        self._utt_announced = False
        self._engine_silence_sec = 0.0

    def _trailing_silence_sec(self) -> float:
        vad_sil = float(getattr(self.endpoint, "trailing_silence_sec", 0.0) or 0.0)
        return max(vad_sil, float(getattr(self, "_engine_silence_sec", 0.0)))

    def _note_chunk_energy(self, chunk: np.ndarray) -> None:
        """Engine-side silence clock — VAD may jump 0→cut inside one feed()."""
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return
        rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
        floor = float(getattr(config, "VAD_SPEECH_RMS_MIN", 0.035)) * 0.45
        if rms < floor:
            self._engine_silence_sec += chunk.size / float(config.SAMPLE_RATE)
        else:
            self._engine_silence_sec = 0.0

    def _clear_speculate(self) -> None:
        self._spec = None

    def _maybe_speculate(self) -> None:
        if not bool(getattr(config, "ASR_SPECULATE_ENABLED", True)):
            return
        if not self.in_speech or self.buf.size <= 0:
            return
        sil = self._trailing_silence_sec()
        # Speech resumed → abandon stale speculative job
        if sil < 0.08:
            self._clear_speculate()
            return
        if self._spec is not None:
            return
        need = float(getattr(config, "ASR_SPECULATE_SILENCE_SEC", 0.25))
        # Also kick when partial frozen (pause) even if VAD silence counter lagged
        paused = self._unchanged_partials >= 1 and bool(
            (self._last_partial_text or "").strip()
        )
        if sil < need and not (paused and sil >= need * 0.5):
            return
        min_samples = int(0.35 * config.SAMPLE_RATE)
        if self.buf.size < min_samples:
            return
        snap = self.buf.copy()
        job: dict = {
            "token": self._utt_id,
            "n": int(snap.size),
            "t0": time.perf_counter(),
            "done": threading.Event(),
            "res": None,
        }
        self._spec = job

        def run() -> None:
            try:
                job["res"] = self.asr.transcribe_with_2pass(snap)
            except Exception as exc:  # noqa: BLE001
                print(f"[ASR spec error] {exc}")
            finally:
                job["done"].set()

        threading.Thread(target=run, daemon=True).start()
        print(f"[ASR spec start sil={sil:.2f}s samples={snap.size}]")

    def _take_speculate(self, audio: np.ndarray):
        job = self._spec
        self._spec = None
        if job is None:
            return None
        if int(job.get("token", -1)) != self._utt_id:
            return None

        # 1) 快照之后又出现语音 → 投机作废，回退到流式收尾（唯一允许重算的情况）
        n0 = int(job.get("n", 0))
        grew_n = int(audio.size) - n0
        if grew_n > int(0.15 * config.SAMPLE_RATE):
            tail = np.asarray(audio[n0:], dtype=np.float32).reshape(-1)
            rms = (
                float(np.sqrt(np.mean(np.square(tail), dtype=np.float64)))
                if tail.size
                else 0.0
            )
            floor = float(getattr(config, "VAD_SPEECH_RMS_MIN", 0.035)) * 0.45
            # Silence-only growth after snap is expected (VAD floor); speech growth → miss
            if rms >= floor:
                print(f"[ASR spec miss audio+{grew_n} rms={rms:.4f}]")
                return None

        # 2) 快照仍有效 → 只等同一个 job，禁止超时后再 finalize_stream（ASR 付两遍）。
        done = bool(job["done"].is_set())
        age = time.perf_counter() - float(job.get("t0", time.perf_counter()))
        headstart = float(getattr(config, "ASR_SPECULATE_MIN_HEADSTART_SEC", 0.15))
        if not done and age < headstart:
            # 几乎没提前量（强切/极短静音）：投机不划算，直接走流式收尾
            print(f"[ASR spec skip no-headstart age={age:.2f}s]")
            return None
        budget = float(getattr(config, "ASR_SPECULATE_WAIT_SEC", 0.55)) + float(
            getattr(config, "ASR_SPECULATE_LATE_WAIT_SEC", 0.8)
        )
        remain = max(0.05, budget - age)
        t_wait = time.perf_counter()
        if not done:
            job["done"].wait(timeout=remain)
        if not job["done"].is_set():
            # 预算用尽仍在算：再 drain 同一 job，而不是另起一遍 final
            print(f"[ASR spec drain age={age:.2f}s]")
            job["done"].wait(timeout=2.0)
        if not job["done"].is_set():
            print(f"[ASR spec miss timeout age={age:.2f}s]")
            return None
        res = job.get("res")
        if res is None:
            return None
        waited = (time.perf_counter() - t_wait) * 1000
        print(
            f"[ASR spec hit {res.ms:.0f}ms waited={waited:.0f}ms] "
            f"{(res.text or '').strip()!r}"
        )
        return res

    def _announce_utt(self) -> None:
        if self._utt_announced:
            return
        self._utt_announced = True
        tag = f"/{self.label}" if self.label else ""
        print(f"\n--- 开始说话 / 双轨{tag} utt={self._utt_id} ---")

    def _partial_interval(self) -> float:
        if not config.PARTIAL_ADAPTIVE:
            return float(config.PARTIAL_INTERVAL_SEC)
        start = float(config.PARTIAL_INTERVAL_START_SEC)
        stable = float(config.PARTIAL_INTERVAL_STABLE_SEC)
        after = float(config.PARTIAL_ADAPT_AFTER_SEC)
        if self.tracker.committed or self.audio_sec >= after:
            return stable
        return start

    def _enqueue_speak(self, texts: list[str], mute_cb=None) -> None:  # noqa: ANN001
        if not texts:
            return
        if mute_cb:
            self.tts_q.set_mute_cb(mute_cb)
        for s in texts:
            self._tts_seq += 1
            if self.tts_q.enabled:
                self.tts_q.submit(
                    s,
                    self.tgt_lang,
                    utt_id=self._utt_id,
                    seq=self._tts_seq,
                    version=self._tts_version,
                )
            elif self.play and self.tts:
                if mute_cb:
                    mute_cb(True)
                try:
                    syn, _ = self.tts.speak(s, self.tgt_lang, save_wav=False)
                    self.tts_ms_sum += syn.ms
                    self.stats.tts_ms.append(syn.ms)
                    print(f"[TTS {syn.ms:.0f}ms] {s}")
                finally:
                    if mute_cb:
                        mute_cb(False)

    def _translate_final(self, text: str, mute_cb=None) -> None:  # noqa: ANN001
        pending = (text or "").strip()
        if not pending:
            return
        if should_skip_final_mt(pending, self._forced_truncation):
            print(f"[MT skip unfinished] {pending!r}")
            return
        if self.force_src_lang in ("zh", "en"):
            src = self.force_src_lang
        else:
            src = detect_lang(pending)
        mt = self.translator.translate(pending, src_lang=src)
        self.mt_ms_sum += mt.ms
        self.stats.mt_ms.append(mt.ms)
        self.stats.mt_triggers += 1
        self.stats.mt_reasons.append("final")
        self.tgt_lang = mt.tgt_lang
        now = time.perf_counter()
        if self.stats.first_stable_mt_ms is None and self.utt_t0:
            self.stats.first_stable_mt_ms = (now - self.utt_t0) * 1000
        if getattr(self, "_t_speech_end", 0.0):
            self.stats.t_mt_done_ms = (now - self._t_speech_end) * 1000
        print(f"[MT {mt.ms:.0f}ms reason=final] {pending!r} => {mt.text}")
        if not mt.text:
            return
        self.translated_bits.append(mt.text)
        self._translated_src = pending
        # final_only: one shot whole sentence — avoid "Just..." → Just. / . / .
        speakable = [mt.text.strip()]
        if self.stats.t_first_audio_ms is None and getattr(self, "_t_speech_end", 0.0):
            self.stats.t_first_audio_ms = (time.perf_counter() - self._t_speech_end) * 1000
            self.stats.latency_perceived_ms = self.stats.t_first_audio_ms
        self._enqueue_speak(speakable, mute_cb=mute_cb)

    def _run_partial(
        self,
        audio: np.ndarray | None,
        new_audio: np.ndarray | None,
        seq: int,
        mute_cb=None,  # noqa: ANN001
    ) -> None:
        try:
            if self.asr.is_true_streaming:
                partial = self.asr.feed_stream(
                    new_audio if new_audio is not None else np.zeros(0, np.float32),
                    self._asr_state,
                )
            else:
                assert audio is not None
                partial = self.asr.transcribe_incremental(audio, self._asr_state)
            self.stats.partial_count += 1
            self.stats.asr_ms.append(partial.ms)
            self.stats.asr_model_samples.append(partial.audio_samples)
            if self.stats.first_partial_ms is None and self.utt_t0:
                self.stats.first_partial_ms = (time.perf_counter() - self.utt_t0) * 1000
            text = (partial.text or "").strip()
            if not text:
                return
            self._announce_utt()
            if text == self._last_partial_text:
                self._unchanged_partials += 1
                print(
                    f"[partial {partial.ms:.0f}ms #{seq} samples={partial.audio_samples}] (unchanged)"
                )
            else:
                self._unchanged_partials = 0
                self._last_partial_text = text
                print(
                    f"[partial {partial.ms:.0f}ms #{seq} samples={partial.audio_samples}] {text}"
                )
                if has_repeated_span(text):
                    self._asr_loop = True
                    print("--- ASR 复读检测，准备强切 ---")
                with self._lock:
                    self.tracker.update(text, seq=seq)
            if self._holding_cut:
                dec = self.arbiter.on_partial(text, seq, now=time.perf_counter())
                self._apply_arbiter(dec, mute_cb=mute_cb)
        finally:
            self._partial_busy = False

    def _apply_arbiter(self, dec, mute_cb=None) -> SpeechSegment | None:  # noqa: ANN001
        if dec.action in (ArbiterAction.WAIT_PARTIAL, ArbiterAction.WAIT_COMPLETION):
            self._holding_cut = True
            reopen = getattr(self.endpoint, "reopen_after_hold", None)
            if callable(reopen):
                reopen()
            return None
        if dec.action in (ArbiterAction.CUT, ArbiterAction.CUT_FORCED):
            self._holding_cut = False
            self._forced_truncation = bool(dec.forced_truncation)
            print(
                f"--- 仲裁收句（{dec.reason}"
                f"{', forced' if dec.forced_truncation else ''}）---"
            )
            audio = self.buf.asarray()
            seg = SpeechSegment(
                audio=audio, start_sample=0, end_sample=int(audio.size)
            )
            self._cut_ready = seg
            return seg
        return None

    def _finalize_utt(self, seg: SpeechSegment, mute_cb=None):  # noqa: ANN001
        if not self._utt_announced and not (self._last_partial_text or "").strip():
            self.endpoint.reset()
            self.reset_utt()
            return None

        t_wait = time.perf_counter()
        while self._partial_busy and (time.perf_counter() - t_wait) < 2.0:
            time.sleep(0.02)

        t_cut = time.perf_counter()
        sil = self._trailing_silence_sec()
        sil_cap = float(getattr(config, "VAD_SILENCE_SEC", 0.6))
        self._t_speech_end = t_cut - min(sil if sil > 0 else sil_cap, sil_cap)
        self.stats.t_cut_ms = (t_cut - self._t_speech_end) * 1000

        print("--- 句尾，最终确认 ---")
        self._tts_version += 1
        self._tts_seq += 1
        self.tts_q.invalidate(
            self._utt_id, self._tts_seq, min_version=self._tts_version
        )

        if self.asr.is_true_streaming:
            audio = self.buf.asarray()
        else:
            audio = (
                seg.audio
                if getattr(seg, "audio", None) is not None and seg.audio.size
                else self.buf.asarray()
            )

        spec = self._take_speculate(audio)
        if spec is not None:
            final = spec
            self.stats.speculate_hit = True
            # Live stream abandoned; next utt gets fresh AsrIncState
            self._asr_state.finished = True
        else:
            final = self.asr.finalize_stream(audio, self._asr_state)
            self.stats.speculate_hit = False

        self.stats.asr_ms.append(final.ms)
        self.stats.asr_model_samples.append(final.audio_samples)
        self.stats.t_asr_done_ms = (time.perf_counter() - self._t_speech_end) * 1000
        raw_final = (final.text or "").strip()
        full = prefer_final_asr(self._last_partial_text, raw_final)
        tag = ""
        if self.stats.speculate_hit:
            tag = " spec"
        elif getattr(self.asr, "_2pass", False) and final.ms > 0:
            tag = " 2pass"
        if full != raw_final:
            print(
                f"[ASR final {final.ms:.0f}ms samples={final.audio_samples}{tag}] "
                f"{raw_final!r} → keep {full!r} (partial longer)"
            )
        else:
            print(
                f"[ASR final {final.ms:.0f}ms samples={final.audio_samples}{tag}] {full}"
            )

        if not full:
            if self._utt_announced:
                print("--- 空识别，跳过翻译/播报 ---")
            self.endpoint.reset()
            self.reset_utt()
            return None

        content = re.sub(r"[^\w\u4e00-\u9fff]+", "", full, flags=re.UNICODE)
        min_chars = int(getattr(config, "MIN_FINAL_ASR_CHARS", 2))
        if len(content) < min_chars:
            print(f"--- 过短识别，跳过 MT/TTS ({full!r}) ---")
            self.stats.source = full
            self.stats.translated = ""
            if self.utt_t0:
                self.stats.utt_wall_ms = (time.perf_counter() - self.utt_t0) * 1000
            out = self.stats
            self.endpoint.reset()
            self.reset_utt()
            return out

        with self._lock:
            self.tracker.finalize(full, translated_src="")
        self.translated_bits.clear()
        self._translate_final(full, mute_cb=mute_cb)

        self.tts_q.wait_idle(timeout=60)
        if self.tts_q.stats.synth_ms:
            self.stats.tts_ms.extend(self.tts_q.stats.synth_ms)
            self.tts_ms_sum = sum(self.tts_q.stats.synth_ms)
            self.tts_q.stats.synth_ms.clear()

        self.stats.source = full
        self.stats.translated = " ".join(self.translated_bits).strip()
        if self.utt_t0:
            self.stats.utt_wall_ms = (time.perf_counter() - self.utt_t0) * 1000
        ft = " forced_trunc" if self._forced_truncation else ""
        spec_s = " spec" if self.stats.speculate_hit else ""
        lat = self.stats.latency_perceived_ms
        print(
            f"[utt] src={self.stats.source!r} | tgt={self.stats.translated!r} "
            f"| first_partial={self.stats.first_partial_ms and round(self.stats.first_partial_ms)}ms "
            f"| first_stable_mt={self.stats.first_stable_mt_ms and round(self.stats.first_stable_mt_ms)}ms "
            f"| perceived={None if lat is None else round(lat)}ms "
            f"| cut={None if self.stats.t_cut_ms is None else round(self.stats.t_cut_ms)}ms "
            f"| asr_done={None if self.stats.t_asr_done_ms is None else round(self.stats.t_asr_done_ms)}ms "
            f"| mt_done={None if self.stats.t_mt_done_ms is None else round(self.stats.t_mt_done_ms)}ms "
            f"| mt_triggers={self.stats.mt_triggers} waits={self.stats.mt_waits} "
            f"| wall={self.stats.utt_wall_ms and round(self.stats.utt_wall_ms)}ms "
            f"| MT累计 {self.mt_ms_sum:.0f}ms TTS累计 {self.tts_ms_sum:.0f}ms "
            f"| policy=final_only{spec_s} "
            f"| intervals={self.stats.partial_interval_used} "
            f"| tts_dropped={self.tts_q.stats.dropped}{ft}"
        )
        out = self.stats
        self.endpoint.reset()
        self.reset_utt()
        return out

    def feed_chunk(
        self,
        chunk: np.ndarray,
        mute_cb=None,  # noqa: ANN001
        allow_async_partial: bool = True,
    ):
        if mute_cb:
            self.tts_q.set_mute_cb(mute_cb)

        if self._cut_ready is not None and self.in_speech:
            seg_ready = self._cut_ready
            self._cut_ready = None
            return self._finalize_utt(seg_ready, mute_cb=mute_cb)

        # Kick speculate on prior silence before this chunk may emit a cut
        if self.in_speech:
            self._maybe_speculate()

        seg = self.endpoint.feed(chunk)

        if self._holding_cut and self.in_speech:
            chunk_f = np.asarray(chunk, dtype=np.float32).reshape(-1)
            self.buf.append(chunk_f)
            self.audio_sec += chunk_f.size / float(config.SAMPLE_RATE)
            reopen = getattr(self.endpoint, "reopen_after_hold", None)
            if callable(reopen):
                reopen()
            dec = self.arbiter.tick(
                self._last_partial_text, self._asr_seq, now=time.perf_counter()
            )
            held = self._apply_arbiter(dec, mute_cb=mute_cb)
            if held is not None:
                return self._finalize_utt(held, mute_cb=mute_cb)

        # 引擎仍在本句时必须吃进当前块：endpoint 收句瞬间 in_speech 已 False，
        # 若只看 endpoint 会丢掉收尾 chunk → final ASR 少末字（多少/怎么样）。
        if self.in_speech or self.endpoint.in_speech or self._holding_cut:
            if not self.in_speech:
                self._begin_utt()
            if not self._holding_cut:
                chunk_f = np.asarray(chunk, dtype=np.float32).reshape(-1)
                self.buf.append(chunk_f)
                self.audio_sec += chunk_f.size / float(config.SAMPLE_RATE)
                self._note_chunk_energy(chunk_f)

            interval = self._partial_interval()
            due = (self.audio_sec - self.last_partial_audio_sec) >= interval
            use_async = bool(allow_async_partial and not self.asr.is_true_streaming)
            if due and self.buf.size > 0 and not self._partial_busy:
                self.stats.partial_interval_used.append(interval)
                self.last_partial_audio_sec = self.audio_sec
                self.last_partial_t = time.perf_counter()
                self._asr_seq += 1
                seq = self._asr_seq
                if self.asr.is_true_streaming:
                    new_audio = self.buf.slice_from(self._asr_state.decoded_samples)
                    audio_snap = None
                else:
                    new_audio = None
                    audio_snap = self.buf.copy() if use_async else self.buf.asarray()
                self._partial_busy = True
                if use_async:
                    threading.Thread(
                        target=self._run_partial,
                        args=(audio_snap, new_audio, seq, mute_cb),
                        daemon=True,
                    ).start()
                else:
                    self._run_partial(audio_snap, new_audio, seq, mute_cb=mute_cb)

            self._maybe_speculate()

            force_unchanged = int(getattr(config, "VAD_FORCE_END_UNCHANGED", 6))
            max_utt = float(getattr(config, "VAD_MAX_UTT_SEC", 15.0))
            empty_abort = float(getattr(config, "VAD_EMPTY_ABORT_SEC", 2.0))
            if seg is None and self.in_speech and self.buf.size and not self._holding_cut:
                has_text = bool((self._last_partial_text or "").strip())
                if (
                    not has_text
                    and empty_abort > 0
                    and self.audio_sec >= empty_abort
                ):
                    if self._utt_announced:
                        print(f"--- 丢弃空录音（噪声 {self.audio_sec:.1f}s）---")
                    self.endpoint.reset()
                    self.reset_utt()
                    return None

                complete_n = int(
                    getattr(
                        config,
                        "VAD_FORCE_END_UNCHANGED_COMPLETE",
                        max(3, force_unchanged - 2),
                    )
                )
                need_unchanged = complete_n if has_text else force_unchanged
                t = self._last_partial_text or ""
                if has_text and is_weekday_incomplete(t):
                    # 补全靠 arbiter 窗口；此处只防 VAD 粘住，勿抬到 9
                    need_unchanged = int(
                        getattr(
                            config,
                            "VAD_FORCE_END_UNCHANGED_WEEKDAY",
                            4,
                        )
                    )

                freeze = bool(
                    has_text and self._unchanged_partials >= need_unchanged
                )
                too_long = max_utt > 0 and self.audio_sec >= max_utt
                if freeze or too_long or self._asr_loop:
                    if self._asr_loop and not freeze and not too_long:
                        print("--- 强制收句（ASR 复读）---")
                    if not has_text:
                        if self._utt_announced:
                            print(
                                f"--- 丢弃空录音（噪声 {self.audio_sec:.1f}s）---"
                            )
                        self.endpoint.reset()
                        self.reset_utt()
                        return None
                    print(
                        f"--- 强制收句（unchanged={self._unchanged_partials} "
                        f"audio={self.audio_sec:.1f}s）---"
                    )
                    audio = self.buf.asarray()
                    seg = SpeechSegment(
                        audio=audio,
                        start_sample=0,
                        end_sample=int(audio.size),
                    )
                    self._forced_truncation = True
                    self.endpoint.reset()
                    return self._finalize_utt(seg, mute_cb=mute_cb)

        if seg is None:
            return None

        if not self._holding_cut:
            dec = self.arbiter.on_vad_candidate(
                self._asr_seq, now=time.perf_counter()
            )
            held = self._apply_arbiter(dec, mute_cb=mute_cb)
            if held is None and self._holding_cut:
                return None
            if held is not None:
                return self._finalize_utt(held, mute_cb=mute_cb)

        return self._finalize_utt(seg, mute_cb=mute_cb)

    def run_on_chunks(
        self, chunks: Iterator[np.ndarray], mute_cb=None, async_partial: bool = False
    ) -> list[DualTrackStats]:
        results: list[DualTrackStats] = []
        for chunk in chunks:
            finished = self.feed_chunk(
                chunk, mute_cb=mute_cb, allow_async_partial=async_partial
            )
            if finished is not None:
                results.append(finished)
        if self.in_speech and self.buf.size:
            pad = np.zeros(
                int(config.VAD_SILENCE_SEC * config.SAMPLE_RATE) + 1600, np.float32
            )
            for _ in range(8):
                finished = self.feed_chunk(
                    pad, mute_cb=mute_cb, allow_async_partial=False
                )
                if finished is not None:
                    results.append(finished)
                    break
            else:
                if self.buf.size:
                    print("--- 强制收尾（仿真） ---")
                    audio = self.buf.asarray()
                    seg = SpeechSegment(
                        audio=audio, start_sample=0, end_sample=int(audio.size)
                    )
                    finished = self._finalize_utt(seg, mute_cb=mute_cb)
                    if finished is not None:
                        results.append(finished)
        return results
