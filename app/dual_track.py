"""精简双轨：流式 ASR 只做字幕草稿；句尾整句 MT/TTS。中途默认不译。"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

from . import config
from .asr_stream import AsrIncState, AsrResult, StreamingAsr, prefer_final_asr
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
from .tts_queue import TtsPlayEvent, TtsQueue
from .early_cut_probe import (
    PROBE_CHECKPOINTS,
    ProbeSnap,
    compute_oracle,
)
from .speculate_lead import estimate_2pass_wall_ms, speculate_trigger_silence_sec
from .vad_stream import SpeechSegment, create_endpoint, vad_backend_label

_UTT_STATS_BOUND = 16


@dataclass
class SpecOfflineHint:
    """re-arm 仅 offline 命中：CUT 须与 live online finalize 合并。"""

    offline_text: str
    offline_ms: float
    audio_samples: int = 0


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
    # 感知延迟（speech_end → …）；dualsim wall 不是此项
    t_cut_ms: float | None = None
    t_asr_done_ms: float | None = None
    t_mt_done_ms: float | None = None
    # 遗留已弃用：speech_end → MT 入队（非设备首帧音频）
    t_first_audio_ms: float | None = None
    # 与遗留 t_first_audio_ms 同义（迁移用显式命名）
    t_mt_enqueue_ms: float | None = None
    # speech_end → MT ready（遗留 latency_perceived_ms；展示为 mt_ready）
    latency_perceived_ms: float | None = None
    # speech_end → 本句 TTS 首段非空 PCM 就绪
    t_tts_pcm_ms: float | None = None
    # speech_end → 设备回调首帧非零 PCM（真实 device TTFA）
    t_first_audio_callback_ms: float | None = None
    latency_device_ttfa_ms: float | None = None
    # MT ready → 设备首帧音频
    mt_ready_to_callback_ms: float | None = None
    tts_active_underruns: int | None = None
    tts_samples_expected: int | None = None
    tts_samples_written: int | None = None
    tts_persistent: bool | None = None
    tts_stream_reused: bool | None = None
    tts_interrupted: bool | None = None
    speculate_hit: bool = False
    source: str = ""
    translated: str = ""
    # 观测（harvest）：postedit 前 MT 文本；final MT 跑完前为 null
    mt_raw: str | None = None
    # final 二次切分后的子句（仅 >=2 句时有值）；旧字段 src/tgt 不变
    sub_lines: list[dict] | None = None
    partial_interval_used: list[float] = field(default_factory=list)
    utt_id: int = 0
    # 只读 early-cut 探针 oracle（未开 ENDPOINT_EARLY_CUT_PROBE 则为 None）
    early_cut_probe: dict | None = None
    audio_sec_final: float | None = None
    # 实麦自检（只读；见 app/mic_probe.py）
    probe_open_peak: float | None = None
    probe_open_rms: float | None = None
    probe_open_prob: float | None = None
    probe_emit_path: str | None = None
    probe_cut_reason: str | None = None
    probe_trailing_silence_sec: float | None = None


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
        # 双工固定方向（mic=zh, loopback=en）；None → detect_lang
        self.force_src_lang = force_src_lang
        self.label = label or ""
        # 仅旁路（如 HarvestWriter / MicProbe）；不在关键延迟路径上。
        self.on_utt_final = None
        self._mic_probe = None
        self.endpoint = create_endpoint()
        tag = f" {self.label}" if self.label else ""
        print(f"[vad{tag}] backend={vad_backend_label(self.endpoint)}")
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
            on_play_event=self._on_tts_play_event,
        )
        # reset_utt 后迟到的 TTS 事件：按 utt_id 索引（有界）
        self._utt_stats: dict[int, DualTrackStats] = {}
        self._utt_speech_end: dict[int, float] = {}
        # 会话级投机统计（额外 CPU / cancel）
        self.spec_counters = {
            "starts": 0,
            "hits": 0,
            "cancel_speech": 0,
            "miss_grow": 0,
            "miss_timeout": 0,
            "skip_headstart": 0,
            "restarts": 0,
            "cpu_ms": 0.0,  # 投机任务墙钟之和
        }
        self.reset_utt()
        self._probe_reset()

    def _probe_reset(self) -> None:
        self._probe_snaps: dict[float, ProbeSnap] = {}
        self._probe_resume_after: set[float] = set()
        self._probe_max_sil = 0.0

    def _probe_tick(self) -> None:
        """只读：在静音检查点快照；不切句、不 cancel 投机。"""
        if not bool(getattr(config, "ENDPOINT_EARLY_CUT_PROBE", False)):
            return
        if not self.in_speech:
            return
        sil = float(self._trailing_silence_sec())
        # 已进入探针带后又恢复说话
        if self._probe_max_sil >= 0.30 and sil + 0.02 < self._probe_max_sil:
            for cp in list(self._probe_snaps.keys()):
                if sil < cp <= self._probe_max_sil:
                    self._probe_resume_after.add(cp)
                    snap = self._probe_snaps.get(cp)
                    if snap is not None:
                        snap.resumed_after = True
        if sil > self._probe_max_sil:
            self._probe_max_sil = sil
        if sil < 0.30 or sil > 0.45 + 1e-6:
            return
        for cp in PROBE_CHECKPOINTS:
            if cp in self._probe_snaps:
                continue
            if sil + 1e-6 < cp:
                continue
            # 静音首次到达该检查点
            spec = self._spec
            spec_exists = spec is not None
            spec_done = bool(spec_exists and spec.get("done") and spec["done"].is_set())
            spec_text = ""
            spec_ms = None
            if spec_done and spec is not None:
                res = spec.get("res")
                if res is not None:
                    spec_text = (getattr(res, "text", None) or "") or ""
                    t0 = float(spec.get("t0") or 0.0)
                    if t0 > 0:
                        spec_ms = (time.perf_counter() - t0) * 1000
            self._probe_snaps[cp] = ProbeSnap(
                checkpoint_sec=cp,
                silence_sec=sil,
                audio_sec=float(self.audio_sec),
                utt_id=int(self._utt_id),
                spec_exists=spec_exists,
                spec_done=spec_done,
                spec_text=spec_text,
                spec_ms=spec_ms,
                partial_text=(self._last_partial_text or ""),
            )

    def _probe_finalize(self, final_src: str) -> None:
        if not bool(getattr(config, "ENDPOINT_EARLY_CUT_PROBE", False)):
            return
        # 若曾穿过探针带，切句时确保存在 0.45 快照
        sil = float(self._trailing_silence_sec())
        if 0.45 not in self._probe_snaps and sil >= 0.40:
            # 由当前状态合成最终检查点（仍只读）
            self._probe_max_sil = max(self._probe_max_sil, 0.45)
            # 临时允许记录 0.45
            old_sil_note = sil
            # 伪造走 tick 路径：手动记录
            spec = self._spec
            spec_exists = spec is not None
            spec_done = bool(spec_exists and spec.get("done") and spec["done"].is_set())
            spec_text = ""
            spec_ms = None
            if spec_done and spec is not None:
                res = spec.get("res")
                if res is not None:
                    spec_text = (getattr(res, "text", None) or "") or ""
                    t0 = float(spec.get("t0") or 0.0)
                    if t0 > 0:
                        spec_ms = (time.perf_counter() - t0) * 1000
            self._probe_snaps[0.45] = ProbeSnap(
                checkpoint_sec=0.45,
                silence_sec=max(old_sil_note, 0.45),
                audio_sec=float(self.audio_sec),
                utt_id=int(self._utt_id),
                spec_exists=spec_exists,
                spec_done=spec_done,
                spec_text=spec_text,
                spec_ms=spec_ms,
                partial_text=(self._last_partial_text or ""),
            )
        oracle = compute_oracle(
            self._probe_snaps,
            final_src,
            resume_after=self._probe_resume_after,
        )
        self.stats.early_cut_probe = oracle.to_dict()
        self.stats.audio_sec_final = float(self.audio_sec)

    def _prune_utt_maps(self) -> None:
        if len(self._utt_stats) <= _UTT_STATS_BOUND:
            return
        # 丢弃最旧键（最小 utt_id）
        for k in sorted(self._utt_stats.keys())[: len(self._utt_stats) - _UTT_STATS_BOUND]:
            self._utt_stats.pop(k, None)
            self._utt_speech_end.pop(k, None)

    def _register_utt_stats(self, stats: DualTrackStats) -> None:
        uid = int(getattr(stats, "utt_id", 0) or self._utt_id)
        stats.utt_id = uid
        self._utt_stats[uid] = stats
        t_end = float(getattr(self, "_t_speech_end", 0.0) or 0.0)
        if t_end > 0:
            self._utt_speech_end[uid] = t_end
        self._prune_utt_maps()

    def _notify_utt_final(self, stats: DualTrackStats) -> None:
        """旁路钩子（harvest）。不得向 finalize 抛异常。"""
        cb = getattr(self, "on_utt_final", None)
        if not callable(cb):
            return
        try:
            cb(stats)
        except Exception as exc:  # noqa: BLE001 — 绝不影响采集
            print(f"[harvest hook error] {exc}")

    def _on_tts_play_event(self, ev: TtsPlayEvent) -> None:
        """填充拥有该 TTS 任务的 utt 的 device-TTFA 字段（reset 后仍安全）。"""
        st = self._utt_stats.get(int(ev.utt_id))
        if st is None:
            return
        t_end = self._utt_speech_end.get(int(ev.utt_id), 0.0)
        st.tts_active_underruns = int(ev.active_underruns)
        st.tts_samples_expected = int(ev.samples_expected)
        st.tts_samples_written = int(ev.samples_written)
        st.tts_persistent = bool(ev.persistent)
        st.tts_stream_reused = bool(ev.stream_reused)
        st.tts_interrupted = bool(ev.interrupted)
        if t_end <= 0:
            return
        if ev.first_pcm_ready_t is not None and st.t_tts_pcm_ms is None:
            st.t_tts_pcm_ms = (ev.first_pcm_ready_t - t_end) * 1000
        # 保留首次 callback，即便后续 utt 的 invalidate() 停播 —
        # device TTFA 已在 barge-in 之前发生。
        if ev.callback_valid and ev.first_callback_t is not None:
            cb_ms = (ev.first_callback_t - t_end) * 1000
            if st.t_first_audio_callback_ms is None or cb_ms < st.t_first_audio_callback_ms:
                st.t_first_audio_callback_ms = cb_ms
                st.latency_device_ttfa_ms = cb_ms
            if st.t_mt_done_ms is not None and st.latency_device_ttfa_ms is not None:
                st.mt_ready_to_callback_ms = (
                    st.latency_device_ttfa_ms - st.t_mt_done_ms
                )

    def wait_tts_idle(self, timeout: float = 120.0) -> None:
        """阻塞直到 TTS 队列排空（迟到事件已应用）。"""
        self.tts_q.wait_idle(timeout=timeout)

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
        self._hold_t0 = 0.0
        self._asr_loop = False
        self._loop_hits = 0
        self._cut_ready: SpeechSegment | None = None
        self._spec: dict | None = None
        self._spec_starts_utt = 0
        self._engine_silence_sec = 0.0
        self._probe_open_peak: float | None = None
        self._probe_open_rms: float | None = None
        self._probe_open_prob: float | None = None
        self._probe_emit_path: str | None = None
        self._probe_cut_reason: str | None = None
        self.arbiter.reset()
        self._probe_reset()

    def _capture_open_probe(self) -> None:
        """从 endpoint 抄开说窗指标（只读）。"""
        ep = self.endpoint
        peak = getattr(ep, "open_peak", None)
        if peak is None:
            return
        self._probe_open_peak = peak
        self._probe_open_rms = getattr(ep, "open_rms", None)
        self._probe_open_prob = getattr(ep, "open_prob", None)

    def _capture_emit_path(self) -> None:
        path = getattr(self.endpoint, "last_emit_path", None)
        if path:
            self._probe_emit_path = str(path)

    def _stamp_probe_on_stats(self, out: DualTrackStats) -> None:
        out.probe_open_peak = self._probe_open_peak
        out.probe_open_rms = self._probe_open_rms
        out.probe_open_prob = self._probe_open_prob
        out.probe_emit_path = self._probe_emit_path
        out.probe_cut_reason = self._probe_cut_reason
        # 任务书要 endpoint.trailing_silence_sec（非引擎侧静音钟）
        out.probe_trailing_silence_sec = float(
            getattr(self.endpoint, "trailing_silence_sec", 0.0) or 0.0
        )

    def _note_empty_abort_probe(self) -> None:
        probe = getattr(self, "_mic_probe", None)
        if probe is None:
            return
        probe.note_empty_abort(
            audio_sec=float(self.audio_sec),
            open_peak=self._probe_open_peak,
            open_rms=self._probe_open_rms,
            open_prob=self._probe_open_prob,
            emit_path=self._probe_emit_path
            or getattr(self.endpoint, "last_emit_path", None),
        )

    def _begin_utt(self, *, preserve_hold: bool = False) -> None:
        """开始新一句。

        preserve_hold=True：VAD 已发出切分候选且仲裁在等待 —
        勿清空 holding_cut / arbiter（ami_000 swallow 类）。
        """
        keep_hold = bool(preserve_hold and self._holding_cut)
        hold_t0 = float(getattr(self, "_hold_t0", 0.0) or 0.0)
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
        self._asr_loop = False
        self._loop_hits = 0
        self._cut_ready = None
        self._spec = None
        self._spec_starts_utt = 0
        if not keep_hold:
            self._holding_cut = False
            self._hold_t0 = 0.0
            self.arbiter.reset()
        else:
            self._hold_t0 = hold_t0 or time.perf_counter()
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
        self._probe_open_peak = None
        self._probe_open_rms = None
        self._probe_open_prob = None
        self._probe_emit_path = None
        self._probe_cut_reason = None
        self._probe_reset()
        self._capture_open_probe()

    def _trailing_silence_sec(self) -> float:
        vad_sil = float(getattr(self.endpoint, "trailing_silence_sec", 0.0) or 0.0)
        return max(vad_sil, float(getattr(self, "_engine_silence_sec", 0.0)))

    def _note_chunk_energy(self, chunk: np.ndarray) -> None:
        """引擎侧静音钟 — VAD 可能在单次 feed() 内从 0 跳到 cut。"""
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return
        rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
        floor = float(getattr(config, "VAD_SPEECH_RMS_MIN", 0.035)) * 0.45
        if rms < floor:
            self._engine_silence_sec += chunk.size / float(config.SAMPLE_RATE)
        else:
            self._engine_silence_sec = 0.0

    def _note_spec_cpu(self, job: dict | None) -> None:
        if not job:
            return
        wall = float(job.get("wall_ms") or 0.0)
        if wall <= 0.0 and job.get("t0"):
            wall = (time.perf_counter() - float(job["t0"])) * 1000.0
        if wall > 0.0:
            self.spec_counters["cpu_ms"] = (
                float(self.spec_counters.get("cpu_ms", 0.0)) + wall
            )

    def _clear_speculate(self, *, reason: str = "") -> None:
        job = self._spec
        if job is not None and reason == "speech":
            self.spec_counters["cancel_speech"] = (
                int(self.spec_counters.get("cancel_speech", 0)) + 1
            )
        if job is not None:
            self._note_spec_cpu(job)
        self._spec = None

    def _maybe_speculate(self) -> None:
        if not bool(getattr(config, "ASR_SPECULATE_ENABLED", True)):
            return
        if not self.in_speech or self.buf.size <= 0:
            return
        sil = self._trailing_silence_sec()
        min_sil = float(getattr(config, "ASR_SPECULATE_MIN_SILENCE_SEC", 0.08))
        # 恢复说话 → 放弃过期投机任务
        if sil < min_sil:
            self._clear_speculate(reason="speech")
            return
        if self._spec is not None:
            return
        max_starts = int(getattr(config, "ASR_SPECULATE_MAX_STARTS", 3))
        if int(getattr(self, "_spec_starts_utt", 0)) >= max_starts:
            return
        audio_sec = float(self.buf.size) / float(config.SAMPLE_RATE)
        need = speculate_trigger_silence_sec(
            audio_sec,
            adaptive=bool(getattr(config, "ASR_SPECULATE_ADAPTIVE_LEAD", False)),
            vad_silence_sec=float(config.VAD_SILENCE_SEC),
            fixed_silence_sec=float(
                getattr(config, "ASR_SPECULATE_SILENCE_SEC", 0.25)
            ),
            min_silence_sec=min_sil,
            margin_sec=float(
                getattr(config, "ASR_SPECULATE_ADAPTIVE_MARGIN_SEC", 0.05)
            ),
        )
        pred_ms = estimate_2pass_wall_ms(audio_sec)
        # partial 冻结（停顿）时也触发，即便 VAD 静音计数滞后
        paused = self._unchanged_partials >= 1 and bool(
            (self._last_partial_text or "").strip()
        )
        paused_need = max(min_sil, need * 0.5)
        if sil < need and not (paused and sil >= paused_need):
            return
        min_samples = int(0.35 * config.SAMPLE_RATE)
        if self.buf.size < min_samples:
            return
        starts_before = int(getattr(self, "_spec_starts_utt", 0))
        is_restart = starts_before > 0
        rearm_mode = str(
            getattr(config, "ASR_SPECULATE_REARM_MODE", "offline")
        ).lower()
        if is_restart and rearm_mode == "off":
            return
        use_offline = bool(is_restart and rearm_mode == "offline")
        snap = self.buf.copy()
        job: dict = {
            "token": self._utt_id,
            "n": int(snap.size),
            "t0": time.perf_counter(),
            "done": threading.Event(),
            "res": None,
            "restart": is_restart,
            "kind": "offline" if use_offline else "full",
            "wall_ms": 0.0,
        }
        self._spec = job
        self._spec_starts_utt = starts_before + 1
        self.spec_counters["starts"] = int(self.spec_counters.get("starts", 0)) + 1
        if job["restart"]:
            self.spec_counters["restarts"] = (
                int(self.spec_counters.get("restarts", 0)) + 1
            )

        def run() -> None:
            try:
                if use_offline:
                    res = self.asr.transcribe_offline_only(snap)
                    job["res"] = res
                    job["offline_text"] = (res.text or "").strip()
                    job["offline_ms"] = float(res.ms)
                else:
                    job["res"] = self.asr.transcribe_with_2pass(snap)
            except Exception as exc:  # noqa: BLE001
                print(f"[ASR spec error] {exc}")
            finally:
                job["wall_ms"] = (time.perf_counter() - float(job["t0"])) * 1000.0
                job["done"].set()

        threading.Thread(target=run, daemon=True).start()
        kind_s = f" kind={job['kind']}" if is_restart else ""
        print(
            f"[ASR spec start sil={sil:.2f}s need={need:.2f}s "
            f"audio_sec={audio_sec:.2f} pred_decode_ms={pred_ms:.0f} "
            f"samples={snap.size} utt_starts={self._spec_starts_utt}{kind_s}]"
        )

    def _take_speculate(self, audio: np.ndarray):
        job = self._spec
        self._spec = None
        if job is None:
            return None
        if int(job.get("token", -1)) != self._utt_id:
            self._note_spec_cpu(job)
            return None

        # 1) 快照之后又出现语音 → 投机作废，回退到流式收尾（唯一允许重算的情况）
        n0 = int(job.get("n", 0))
        grew_n = int(audio.size) - n0
        if grew_n > int(0.15 * config.SAMPLE_RATE):
            tail = np.asarray(audio[n0:], dtype=np.float32).reshape(-1)
            use_p90 = bool(getattr(config, "ASR_SPEC_MISS_GROW_USE_P90", True))
            rms = 0.0
            if tail.size:
                if use_p90:
                    # 30ms 帧 p90：避免「语音+长尾静音」均值被稀释漏判 miss_grow
                    win = max(1, int(0.03 * config.SAMPLE_RATE))
                    if tail.size < win:
                        rms = float(
                            np.sqrt(np.mean(np.square(tail), dtype=np.float64))
                        )
                    else:
                        n_fr = int(tail.size // win)
                        frames = tail[: n_fr * win].reshape(n_fr, win)
                        fr = np.sqrt(
                            np.mean(np.square(frames), axis=1, dtype=np.float64)
                        )
                        rms = float(np.percentile(fr, 90))
                else:
                    rms = float(
                        np.sqrt(np.mean(np.square(tail), dtype=np.float64))
                    )
            floor = float(getattr(config, "VAD_SPEECH_RMS_MIN", 0.035)) * 0.45
            # 快照后仅静音增长属预期（VAD 底限）；有语音增长 → miss
            if rms >= floor:
                self.spec_counters["miss_grow"] = (
                    int(self.spec_counters.get("miss_grow", 0)) + 1
                )
                tag = "rms_p90" if use_p90 else "rms"
                print(f"[ASR spec miss audio+{grew_n} {tag}={rms:.4f}]")
                self._note_spec_cpu(job)
                return None

        # 2) 快照仍有效 → 只等同一个 job，禁止超时后再 finalize_stream（ASR 付两遍）。
        done = bool(job["done"].is_set())
        age = time.perf_counter() - float(job.get("t0", time.perf_counter()))
        headstart = float(getattr(config, "ASR_SPECULATE_MIN_HEADSTART_SEC", 0.15))
        # 仅首枪：re-arm 已付 cancel_speech；跳过会
        # 强制新的串行 2pass（常比 drain 在途快照更慢）。
        if (
            not done
            and age < headstart
            and not bool(job.get("restart"))
        ):
            # 几乎没提前量（强切/极短静音）：投机不划算，直接走流式收尾
            self.spec_counters["skip_headstart"] = (
                int(self.spec_counters.get("skip_headstart", 0)) + 1
            )
            print(f"[ASR spec skip no-headstart age={age:.2f}s]")
            self._note_spec_cpu(job)
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
            self.spec_counters["miss_timeout"] = (
                int(self.spec_counters.get("miss_timeout", 0)) + 1
            )
            print(f"[ASR spec miss timeout age={age:.2f}s]")
            self._note_spec_cpu(job)
            return None
        res = job.get("res")
        self._note_spec_cpu(job)
        if res is None:
            return None
        waited = (time.perf_counter() - t_wait) * 1000
        self.spec_counters["hits"] = int(self.spec_counters.get("hits", 0)) + 1
        if str(job.get("kind") or "full") == "offline":
            off_t = (
                str(job.get("offline_text") or "").strip()
                or (getattr(res, "text", "") or "").strip()
            )
            off_ms = float(
                job.get("offline_ms")
                if job.get("offline_ms") is not None
                else getattr(res, "ms", 0.0)
            )
            print(
                f"[ASR spec offline-hint {off_ms:.0f}ms waited={waited:.0f}ms] "
                f"{off_t!r}"
            )
            return SpecOfflineHint(
                offline_text=off_t,
                offline_ms=off_ms,
                audio_samples=int(
                    getattr(res, "audio_samples", 0) or job.get("n", 0)
                ),
            )
        spec_t = (res.text or "").strip()
        partial = (self._last_partial_text or "").strip()
        # 早投机短稿 vs 已变长的 live partial：拒绝过期 hit（ASR_SPEC_REJECT_STALE 可关）
        if (
            bool(getattr(config, "ASR_SPEC_REJECT_STALE", True))
            and partial
            and spec_t
            and len(partial) >= len(spec_t) + 12
            and spec_t.lower() not in partial.lower()
        ):
            self.spec_counters["miss_stale"] = (
                int(self.spec_counters.get("miss_stale", 0)) + 1
            )
            print(
                f"[ASR spec miss stale short={spec_t[:40]!r} "
                f"partial={partial[:48]!r}]"
            )
            self._note_spec_cpu(job)
            return None
        print(
            f"[ASR spec hit {res.ms:.0f}ms waited={waited:.0f}ms] "
            f"{spec_t!r}"
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

    def _final_mt_clauses(self, pending: str) -> list[str]:
        """仅 final：ASR 原生文本上有「非串尾句末 + 其后足够长」则拆子句。

        切分决策不调用标点模型（展示标点只在 _finalize_utt 跑一次）。
        """
        pending = (pending or "").strip()
        if not pending:
            return []
        from .retranscribe import has_splitworthy_mid_end, split_sentences

        if not has_splitworthy_mid_end(pending):
            return [pending]
        parts = split_sentences(pending)
        return parts if len(parts) >= 2 else [pending]

    def _translate_final(self, text: str, mute_cb=None) -> None:  # noqa: ANN001
        pending = (text or "").strip()
        if not pending:
            return
        if should_skip_final_mt(pending, self._forced_truncation):
            print(f"[MT skip unfinished] {pending!r}")
            return
        clauses = self._final_mt_clauses(pending)
        speakable: list[str] = []
        sub_lines: list[dict] = []
        raw_bits: list[str] = []
        now = time.perf_counter()
        for clause in clauses:
            clause = (clause or "").strip()
            if not clause:
                continue
            if self.force_src_lang in ("zh", "en"):
                src = self.force_src_lang
            else:
                src = detect_lang(clause)
            mt = self.translator.translate(clause, src_lang=src)
            self.mt_ms_sum += mt.ms
            self.stats.mt_ms.append(mt.ms)
            self.stats.mt_triggers += 1
            self.stats.mt_reasons.append("final")
            self.tgt_lang = mt.tgt_lang
            raw_bits.append(getattr(mt, "mt_raw", None) or mt.text or "")
            print(f"[MT {mt.ms:.0f}ms reason=final] {clause!r} => {mt.text}")
            if not mt.text:
                continue
            self.translated_bits.append(mt.text)
            speakable.append(mt.text.strip())
            sub_lines.append({"src": clause, "tgt": mt.text})
        self.stats.mt_raw = " ".join(raw_bits).strip() if raw_bits else None
        # 仅真正拆开时暴露 sub_lines（单句保持 null，兼容旧消费者）
        self.stats.sub_lines = sub_lines if len(sub_lines) >= 2 else None
        self._translated_src = pending
        if self.stats.first_stable_mt_ms is None and self.utt_t0:
            self.stats.first_stable_mt_ms = (now - self.utt_t0) * 1000
        if getattr(self, "_t_speech_end", 0.0):
            self.stats.t_mt_done_ms = (time.perf_counter() - self._t_speech_end) * 1000
        if not speakable:
            return
        # final 可多句播：partial 仍禁止碎切（见历史 Just. / . / .）
        # 遗留：「first audio」实为 MT 入队 / MT-ready 代理。
        if self.stats.t_first_audio_ms is None and getattr(self, "_t_speech_end", 0.0):
            ready_ms = (time.perf_counter() - self._t_speech_end) * 1000
            self.stats.t_first_audio_ms = ready_ms  # 已弃用别名
            self.stats.t_mt_enqueue_ms = ready_ms
            self.stats.latency_perceived_ms = ready_ms  # mt_ready / perceived_mt
        # 入队前先注册，避免迟到/过快 TTS 事件找不到 map。
        self.stats.utt_id = self._utt_id
        self._register_utt_stats(self.stats)
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
                    self._loop_hits = int(getattr(self, "_loop_hits", 0) or 0) + 1
                else:
                    self._loop_hits = 0
                guard = str(
                    getattr(config, "ASR_LOOP_GUARD_MODE", "soft") or "soft"
                ).strip().lower()
                streak = max(1, int(getattr(config, "ASR_LOOP_STREAK", 2) or 2))
                if guard == "soft":
                    if self._loop_hits >= streak:
                        self._asr_loop = True
                        print("--- ASR 复读检测，准备强切 ---")
                elif has_repeated_span(text):
                    # legacy：一次命中即 latch（与旧行为一致）
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
            if not self._holding_cut:
                self._hold_t0 = time.perf_counter()
            self._holding_cut = True
            reopen = getattr(self.endpoint, "reopen_after_hold", None)
            if callable(reopen):
                reopen()
            return None
        if dec.action in (ArbiterAction.CUT, ArbiterAction.CUT_FORCED):
            self._holding_cut = False
            self._hold_t0 = 0.0
            self._forced_truncation = bool(dec.forced_truncation)
            # 仲裁 reason 透传；探针只记不改行为
            self._probe_cut_reason = str(getattr(dec, "reason", "") or "arbiter")
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

    def _force_hold_cut(self, reason: str) -> SpeechSegment | None:
        """释放卡住的仲裁 hold → 必须进入 finalize（不可静默丢弃）。"""
        if not self._holding_cut:
            return None
        print(f"--- 仲裁收句（{reason}, hold_timeout）---")
        self._holding_cut = False
        self._hold_t0 = 0.0
        self.arbiter.reset()
        # hold_timeout / flush_hold → 探针 cut_reason
        if reason == "flush_hold":
            self._probe_cut_reason = "flush"
        else:
            self._probe_cut_reason = "hold_timeout"
        # reset 仲裁等效 cut_done；从 buf/seg 构造 seg
        audio = self.buf.asarray()
        if audio.size <= 0:
            return None
        seg = SpeechSegment(audio=audio, start_sample=0, end_sample=int(audio.size))
        self._cut_ready = seg
        return seg

    def _finalize_utt(self, seg: SpeechSegment, mute_cb=None):  # noqa: ANN001
        # 此前：VAD 在任意 partial 前切句时静默 return None →
        # emitted_swallowed。现始终跑 final ASR 以保证 utt 有结尾。
        if not self.in_speech:
            # 引擎未开说时从 segment 播种（配合 feed_chunk 修复）
            self.in_speech = True
            if self.utt_t0 <= 0:
                self.utt_t0 = time.perf_counter()
            if self.buf.size <= 0 and getattr(seg, "audio", None) is not None:
                audio0 = np.asarray(seg.audio, dtype=np.float32).reshape(-1)
                if audio0.size:
                    self.buf.append(audio0)
                    self.audio_sec = audio0.size / float(config.SAMPLE_RATE)

        # Phase C：无句末标点的不足 1 秒碎片 hold 在同一句
        #（与后续语音合并）。VAD_MIN_UTT_SEC=0 时关闭。
        min_utt = float(getattr(config, "VAD_MIN_UTT_SEC", 0.0) or 0.0)
        partial = (self._last_partial_text or "").strip()
        has_term = partial.endswith((".", "?", "!", "。", "？", "！"))
        max_utt = float(getattr(config, "VAD_MAX_UTT_SEC", 15.0) or 0.0)
        under_max = max_utt <= 0 or self.audio_sec < max_utt
        if (
            min_utt > 0
            and self.audio_sec < min_utt
            and not has_term
            and under_max
            and not getattr(self, "_forced_truncation", False)
        ):
            reopen = getattr(self.endpoint, "reopen_after_hold", None)
            if callable(reopen):
                reopen()
            else:
                self.endpoint.reset()
                # 保持 in_speech / 保留 buf — 不 reset_utt
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

        # Offline re-arm：online finalize 与 drain offline hint 并行，
        # 使 CUT wall ≈ max(online, offline_remain)，非相加。
        pending = self._spec
        if (
            pending is not None
            and str(pending.get("kind") or "") == "offline"
            and int(pending.get("token", -1)) == self._utt_id
        ):
            online_box: dict = {}

            def _online_job() -> None:
                try:
                    online_box["res"] = self.asr.finalize_online_only(
                        audio, self._asr_state
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[ASR online-finalize error] {exc}")

            th = threading.Thread(target=_online_job, daemon=True)
            th.start()
            spec = self._take_speculate(audio)
            th.join(timeout=3.0)
            online = online_box.get("res")
            if isinstance(spec, SpecOfflineHint) and isinstance(online, AsrResult):
                final = self.asr.merge_online_with_offline_hint(
                    online, spec.offline_text, spec.offline_ms
                )
                self.stats.speculate_hit = True
            elif isinstance(online, AsrResult):
                # Offline miss/timeout — stream 已 flush；只需 offline
                off = self.asr.transcribe_offline_only(audio)
                final = self.asr.merge_online_with_offline_hint(
                    online, off.text, off.ms
                )
                self.stats.speculate_hit = False
            else:
                final = self.asr.finalize_stream(audio, self._asr_state)
                self.stats.speculate_hit = False
        else:
            spec = self._take_speculate(audio)
            if isinstance(spec, SpecOfflineHint):
                online = self.asr.finalize_online_only(audio, self._asr_state)
                final = self.asr.merge_online_with_offline_hint(
                    online, spec.offline_text, spec.offline_ms
                )
                self.stats.speculate_hit = True
            elif isinstance(spec, AsrResult):
                final = spec
                self.stats.speculate_hit = True
                # 放弃 live stream；下一句新建 AsrIncState
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
            else:
                print("--- 空识别（无 partial），仍记 finalize_empty ---")
            self.stats.source = ""
            self.stats.translated = ""
            if self.utt_t0:
                self.stats.utt_wall_ms = (time.perf_counter() - self.utt_t0) * 1000
            out = self.stats
            out.utt_id = self._utt_id
            out.audio_sec_final = float(self.audio_sec)
            if not self._probe_cut_reason:
                self._probe_cut_reason = "vad_endpoint"
            self._stamp_probe_on_stats(out)
            self._notify_utt_final(out)
            self.endpoint.reset()
            self.reset_utt()
            return out

        content = re.sub(r"[^\w\u4e00-\u9fff]+", "", full, flags=re.UNICODE)
        min_chars = int(getattr(config, "MIN_FINAL_ASR_CHARS", 2))
        if len(content) < min_chars:
            print(f"--- 过短识别，跳过 MT/TTS ({full!r}) ---")
            self.stats.source = full
            self.stats.translated = ""
            if self.utt_t0:
                self.stats.utt_wall_ms = (time.perf_counter() - self.utt_t0) * 1000
            out = self.stats
            if not self._probe_cut_reason:
                self._probe_cut_reason = "vad_endpoint"
            self._stamp_probe_on_stats(out)
            self._notify_utt_final(out)
            self.endpoint.reset()
            self.reset_utt()
            return out

        with self._lock:
            self.tracker.finalize(full, translated_src="")
        self.translated_bits.clear()
        # MT 吃裸 ASR（切分也只看 ASR 原生标点）；展示标点整句只跑一次。
        # OfflinePunctuation 总会句末化 — 勿把带标点整句喂给 Opus-MT。
        self._translate_final(full, mute_cb=mute_cb)
        display_src = full
        if bool(getattr(config, "ASR_PUNCT_DISPLAY", False)):
            from .punct import add_punctuation

            display_src, punct_ms = add_punctuation(full)
            if punct_ms > 0 and display_src != full:
                print(f"[punct {punct_ms:.0f}ms] {full!r} → {display_src!r}")

        # P0-3：可选 — 不因播放在音频/feed 线程阻塞。
        if bool(getattr(config, "FINALIZE_WAIT_TTS", True)):
            self.tts_q.wait_idle(timeout=60)
        if self.tts_q.stats.synth_ms:
            self.stats.tts_ms.extend(list(self.tts_q.stats.synth_ms))
            self.tts_ms_sum = sum(self.tts_q.stats.synth_ms)
            self.tts_q.stats.synth_ms.clear()

        self.stats.source = display_src
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
            f"| mt_ready={None if lat is None else round(lat)}ms "
            f"| cut={None if self.stats.t_cut_ms is None else round(self.stats.t_cut_ms)}ms "
            f"| asr_done={None if self.stats.t_asr_done_ms is None else round(self.stats.t_asr_done_ms)}ms "
            f"| mt_done={None if self.stats.t_mt_done_ms is None else round(self.stats.t_mt_done_ms)}ms "
            f"| device_ttfa={None if self.stats.latency_device_ttfa_ms is None else round(self.stats.latency_device_ttfa_ms)}ms "
            f"| mt_triggers={self.stats.mt_triggers} waits={self.stats.mt_waits} "
            f"| wall={self.stats.utt_wall_ms and round(self.stats.utt_wall_ms)}ms "
            f"| MT累计 {self.mt_ms_sum:.0f}ms TTS累计 {self.tts_ms_sum:.0f}ms "
            f"| policy=final_only{spec_s} "
            f"| intervals={self.stats.partial_interval_used} "
            f"| tts_dropped={self.tts_q.stats.dropped}{ft}"
        )
        out = self.stats
        out.utt_id = self._utt_id
        out.audio_sec_final = float(self.audio_sec)
        if not self._probe_cut_reason:
            self._probe_cut_reason = "vad_endpoint"
        self._stamp_probe_on_stats(out)
        self._probe_finalize(out.source or full)
        self._register_utt_stats(out)
        self._notify_utt_final(out)
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

        # 本块可能 emit cut 前，基于已有静音触发投机
        if self.in_speech:
            self._maybe_speculate()
            self._probe_tick()

        seg = self.endpoint.feed(chunk)
        if seg is not None:
            self._capture_emit_path()
            self._capture_open_probe()

        if self._holding_cut and self.in_speech:
            chunk_f = np.asarray(chunk, dtype=np.float32).reshape(-1)
            self.buf.append(chunk_f)
            self.audio_sec += chunk_f.size / float(config.SAMPLE_RATE)
            # hold 期间必须更新静音钟 — 否则句中气口
            # 会起 spec job，恢复说话后 sil 仍高、不 cancel，
            # CUT 对过期快照见 miss_grow（weekday holding_cut）。
            self._note_chunk_energy(chunk_f)
            reopen = getattr(self.endpoint, "reopen_after_hold", None)
            if callable(reopen):
                reopen()
            # hold 中恢复说话 → 仲裁可能 CUT 前先丢过期 spec
            self._maybe_speculate()
            now = time.perf_counter()
            hold_lim = float(getattr(config, "ENDPOINT_HOLD_TIMEOUT_SEC", 3.0) or 0.0)
            if (
                hold_lim > 0
                and self._hold_t0 > 0
                and (now - self._hold_t0) >= hold_lim
            ):
                held = self._force_hold_cut("hold_timeout")
            else:
                dec = self.arbiter.tick(
                    self._last_partial_text, self._asr_seq, now=now
                )
                held = self._apply_arbiter(dec, mute_cb=mute_cb)
            if held is not None:
                return self._finalize_utt(held, mute_cb=mute_cb)

        # 引擎仍在本句时必须吃进当前块：endpoint 收句瞬间 in_speech 已 False，
        # 若只看 endpoint 会丢掉收尾 chunk → final ASR 少末字（多少/怎么样）。
        if self.in_speech or self.endpoint.in_speech or self._holding_cut:
            if not self.in_speech:
                # 若已在 hold VAD 候选，保留仲裁状态。
                self._begin_utt(preserve_hold=bool(self._holding_cut))
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
            self._probe_tick()

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
                    self._probe_cut_reason = "empty_abort"
                    self._note_empty_abort_probe()
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
                force_loop = False
                if self._asr_loop and not freeze and not too_long:
                    guard = str(
                        getattr(config, "ASR_LOOP_GUARD_MODE", "soft") or "soft"
                    ).strip().lower()
                    if guard == "soft":
                        # 流式 partial 无否决权：整段 offline 复核后再决定是否强切
                        off = self.asr.transcribe_offline_only(self.buf.asarray())
                        off_t = (off.text or "").strip()
                        if has_repeated_span(off_t):
                            force_loop = True
                            print("--- 强制收句（ASR 复读，offline 复核）---")
                        else:
                            print(
                                f"--- ASR 复读 offline 复核通过，取消强切 "
                                f"({off_t[:48]!r}) ---"
                            )
                            self._asr_loop = False
                            self._loop_hits = 0
                    else:
                        force_loop = True
                        print("--- 强制收句（ASR 复读）---")
                if freeze or too_long or force_loop:
                    if not has_text:
                        if self._utt_announced:
                            print(
                                f"--- 丢弃空录音（噪声 {self.audio_sec:.1f}s）---"
                            )
                        self._probe_cut_reason = "empty_abort"
                        self._note_empty_abort_probe()
                        self.endpoint.reset()
                        self.reset_utt()
                        return None
                    print(
                        f"--- 强制收句（unchanged={self._unchanged_partials} "
                        f"audio={self.audio_sec:.1f}s）---"
                    )
                    if force_loop:
                        self._probe_cut_reason = "asr_loop"
                    elif too_long:
                        self._probe_cut_reason = "max_utt"
                    else:
                        # unchanged freeze：任务表未列；记 unchanged 便于归因
                        self._probe_cut_reason = "unchanged"
                    audio = self.buf.asarray()
                    # P2：复读强切后把末尾播入下一句，避免余音永久丢失
                    keep_tail = bool(
                        force_loop
                        and getattr(config, "ASR_LOOP_KEEP_TAIL", True)
                    )
                    tail = np.zeros(0, dtype=np.float32)
                    if keep_tail and audio.size:
                        n_tail = min(int(audio.size), int(0.8 * config.SAMPLE_RATE))
                        min_tail = int(0.35 * config.SAMPLE_RATE)
                        if n_tail >= min_tail:
                            tail = np.asarray(audio[-n_tail:], dtype=np.float32).copy()
                    seg = SpeechSegment(
                        audio=audio,
                        start_sample=0,
                        end_sample=int(audio.size),
                    )
                    self._forced_truncation = True
                    self.endpoint.reset()
                    out = self._finalize_utt(seg, mute_cb=mute_cb)
                    if keep_tail and tail.size >= int(0.35 * config.SAMPLE_RATE):
                        self._begin_utt()
                        self.in_speech = True
                        if self.utt_t0 <= 0:
                            self.utt_t0 = time.perf_counter()
                        self.buf.append(tail)
                        self.audio_sec = float(tail.size) / float(config.SAMPLE_RATE)
                        reopen = getattr(self.endpoint, "reopen_after_hold", None)
                        if callable(reopen):
                            reopen()
                        print(
                            f"--- ASR 复读强切保留尾音 {tail.size / config.SAMPLE_RATE:.2f}s ---"
                        )
                    return out

        if seg is None:
            return None

        # 短/弱片段：VAD 可能在 engine.in_speech 前 emit segment。
        # 在仲裁 hold 前先 _begin_utt（从 seg 播种 buf），避免
        # _begin_utt 抹掉候选（ami_000 emitted_swallowed）。
        if not self.in_speech:
            self._begin_utt(preserve_hold=False)
            seg_audio = getattr(seg, "audio", None)
            if seg_audio is not None and np.asarray(seg_audio).size:
                a = np.asarray(seg_audio, dtype=np.float32).reshape(-1)
                self.buf.append(a)
                self.audio_sec = a.size / float(config.SAMPLE_RATE)

        if not self._holding_cut:
            dec = self.arbiter.on_vad_candidate(
                self._asr_seq, now=time.perf_counter()
            )
            held = self._apply_arbiter(dec, mute_cb=mute_cb)
            if held is None and self._holding_cut:
                return None
            if held is not None:
                return self._finalize_utt(held, mute_cb=mute_cb)

        if not self._probe_cut_reason:
            self._probe_cut_reason = "vad_endpoint"
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
        if self._holding_cut or (self.in_speech and self.buf.size):
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
                if self._holding_cut:
                    held = self._force_hold_cut("flush_hold")
                    if held is not None:
                        finished = self._finalize_utt(held, mute_cb=mute_cb)
                        if finished is not None:
                            results.append(finished)
                elif self.buf.size:
                    print("--- 强制收尾（仿真） ---")
                    self._probe_cut_reason = "flush"
                    audio = self.buf.asarray()
                    seg = SpeechSegment(
                        audio=audio, start_sample=0, end_sample=int(audio.size)
                    )
                    finished = self._finalize_utt(seg, mute_cb=mute_cb)
                    if finished is not None:
                        results.append(finished)
        return results
