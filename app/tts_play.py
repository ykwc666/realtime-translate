from __future__ import annotations

import atexit
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from piper import PiperVoice

from . import config

# Piper medium 每次 synthesize() 约一整块，音素流
# preroll 赢不了 batch。分句才是 TTFA 杠杆。
_CLAUSE_SPLIT = re.compile(r"(?<=[,，。！？!?.;；])\s+")

# 持久队列项：(generation, pcm_or_sentinel_None)
_PersistItem = tuple[int, np.ndarray | None]


def split_tts_clauses(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(text) if p and p.strip()]
    return parts if parts else [text]


@dataclass
class PlayTiming:
    """设备侧播放指标（callback 路径）。"""

    stream_open_ms: float = 0.0
    # speak_t0 → 首次写入非零 PCM 的 callback（相对 ms）
    ttfa_callback_ms: float = 0.0
    # 末样本写入 → play 调用完全返回
    close_or_drain_ms: float = 0.0
    expected_play_sec: float = 0.0
    actual_callback_play_sec: float = 0.0
    # 旧别名：filled = active_underruns
    underruns: int = 0
    # 仅当 utterance 仍需要 PCM（非 idle 零填充）
    active_underruns: int = 0
    idle_callbacks: int = 0
    samples_written: int = 0
    samples_expected: int = 0
    stop_latency_ms: float = 0.0
    persistent: bool = False
    stream_reused: bool = False
    # perf_counter 绝对时间戳（端到端统计）
    first_callback_t: float | None = None
    first_pcm_ready_t: float | None = None
    # 仅当真实 OutputStream callback 写入非零 PCM 时为 True
    callback_valid: bool = False
    interrupted: bool = False


@dataclass
class TtsResult:
    path: Path | None
    ms: float
    voice: str
    audio: np.ndarray | None = None
    sample_rate: int = 22050
    # 旧指标：speak/synth 起点 → 首段非空 PCM 就绪（非设备 callback）。
    ttfa_ms: float = 0.0
    ttfa_pcm_ms: float = 0.0
    first_pcm_ready_t: float | None = None
    play_timing: PlayTiming | None = None


class TtsPlayer:
    """离线 Piper TTS（无网络）。"""

    def __init__(
        self,
        out_dir: Path | None = None,
        output_device: int | str | None = None,
    ) -> None:
        self.out_dir = out_dir or (config.CACHE_DIR / "tts")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._voices: dict[str, PiperVoice] = {}
        self._voice_lock = threading.Lock()  # _load / 后台预载 互斥
        self._warmed = False
        self._preload_thread: threading.Thread | None = None
        self._io_lock = threading.Lock()  # duplex：mic+环回共用同一播放器
        self._stop = threading.Event()
        self._gen = 0  # stop/invalidate 时递增以丢弃过期 PCM
        self.output_device = self._resolve_device(output_device)
        self.last_play_timing: PlayTiming | None = None
        # 持久 OutputStream 状态（仅 TTS_PERSISTENT_OUTPUT）
        self._persist_stream: sd.OutputStream | None = None
        self._persist_sr: int | None = None
        self._persist_q: queue.Queue[_PersistItem] = queue.Queue()
        self._persist_leftover = np.zeros(0, dtype=np.float32)
        self._persist_lock = threading.Lock()
        self._persist_play_done = threading.Event()
        self._persist_play_done.set()
        self._persist_timing: PlayTiming | None = None
        self._persist_speak_t0 = 0.0
        self._persist_play_gen = -1
        self._persist_need_pcm = False
        self._persist_wrote_nonzero = False
        self._persist_samples = 0
        self._persist_active_underruns = 0
        self._persist_idle_callbacks = 0
        self._persist_last_write_t = 0.0
        self._persist_open_ms = 0.0
        self._persist_first_open = True
        atexit.register(self.close)

    @staticmethod
    def _resolve_device(output_device: int | str | None) -> int | None:
        from .audio_io import resolve_output_device

        if isinstance(output_device, int):
            return output_device
        if isinstance(output_device, str) and output_device.strip():
            return resolve_output_device(output_device)
        return resolve_output_device(None)

    def _load(self, lang: str) -> tuple[PiperVoice, str]:
        with self._voice_lock:
            if lang not in self._voices:
                model = config.PIPER_VOICE_ZH if lang == "zh" else config.PIPER_VOICE_EN
                if not model.exists():
                    raise FileNotFoundError(
                        f"缺少 Piper 音色: {model}\n请运行: python scripts/download_piper_voices.py"
                    )
                cfg = Path(str(model) + ".json")
                self._voices[lang] = PiperVoice.load(
                    str(model),
                    config_path=str(cfg) if cfg.exists() else None,
                    use_cuda=False,
                )
            name = (
                config.PIPER_VOICE_ZH.name
                if lang == "zh"
                else config.PIPER_VOICE_EN.name
            )
            return self._voices[lang], name

    def warmup(self, langs: tuple[str, ...] = ("zh", "en")) -> dict[str, float]:
        """按需加载音色并做一次短合成；未列出的语言仍走懒加载 ``_load``。"""
        sample = {"zh": "测试", "en": "test"}
        costs: dict[str, float] = {}
        for lang in langs:
            text = sample.get(lang)
            if text is None:
                continue
            t0 = time.perf_counter()
            self._load(lang)
            _ = self.synthesize(text, lang, save_wav=False)
            costs[lang] = (time.perf_counter() - t0) * 1000
        try:
            tone = (
                0.05
                * np.sin(
                    2
                    * np.pi
                    * 440.0
                    * np.arange(int(22050 * 0.05), dtype=np.float32)
                    / 22050.0
                )
            ).astype(np.float32)
            self.play_array(tone, 22050, speak_t0=time.perf_counter())
        except Exception:  # noqa: BLE001
            pass
        self._warmed = True
        return costs

    def preload_background(self, langs: tuple[str, ...]) -> None:
        """在后台加载未预热的音色，避免首播路径上同步 ``_load`` 卡 7s+。

        ``dualsim`` / ``file`` / ``ptt`` 同步只预热 en（方向按内容检测），
        同时后台预载 zh；首播若线程未完成，仍会在 ``_load`` 上等锁，但不再
        把整段加载塞进 speak 热路径的「第一次才发现要加载」语义里——调用方
        应在模型就绪后、真正开口前尽早调用本方法。
        """
        need = tuple(lang for lang in langs if lang and lang not in self._voices)
        if not need:
            return
        if self._preload_thread is not None and self._preload_thread.is_alive():
            return

        sample = {"zh": "测试", "en": "test"}

        def _job() -> None:
            for lang in need:
                if self._stop.is_set():
                    return
                try:
                    t0 = time.perf_counter()
                    self._load(lang)
                    text = sample.get(lang)
                    if text:
                        _ = self.synthesize(text, lang, save_wav=False)
                    ms = (time.perf_counter() - t0) * 1000
                    print(f"[TTS] 后台预载完成 {lang}={ms:.0f}ms", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[TTS] 后台预载失败 {lang}: {exc}", flush=True)

        self._preload_thread = threading.Thread(
            target=_job, name="tts-preload-bg", daemon=True
        )
        self._preload_thread.start()

    def wait_preload(self, timeout: float | None = 120.0) -> bool:
        """等后台预载结束。返回是否已结束（超时则为 False）。"""
        th = self._preload_thread
        if th is None:
            return True
        th.join(timeout=timeout)
        return not th.is_alive()

    def close(self) -> None:
        """释放持久流；可多次调用。"""
        self._stop.set()
        th = self._preload_thread
        if th is not None and th.is_alive():
            th.join(timeout=30.0)
        self._gen += 1
        self._persist_need_pcm = False
        self._persist_drain()
        with self._persist_lock:
            stream = self._persist_stream
            self._persist_stream = None
            self._persist_sr = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        self._persist_play_done.set()

    def synthesize(
        self, text: str, lang: str, save_wav: bool = True
    ) -> TtsResult:
        text = (text or "").strip()
        if not text:
            return TtsResult(None, 0.0, "")

        voice, voice_name = self._load(lang)
        t0 = time.perf_counter()
        chunks: list[np.ndarray] = []
        sample_rate = 22050
        for chunk in voice.synthesize(text):
            sample_rate = int(chunk.sample_rate)
            audio = np.asarray(chunk.audio_float_array, dtype=np.float32)
            if audio.size:
                chunks.append(audio)
        ms = (time.perf_counter() - t0) * 1000
        ready_t = time.perf_counter()
        if not chunks:
            return TtsResult(
                None,
                ms,
                voice_name,
                ttfa_ms=ms,
                ttfa_pcm_ms=ms,
                first_pcm_ready_t=ready_t,
            )
        wav = np.concatenate(chunks)
        path = None
        if save_wav:
            path = self.out_dir / f"utt_{int(time.time() * 1000)}.wav"
            sf.write(str(path), wav, sample_rate)
        return TtsResult(
            path,
            ms,
            voice_name,
            audio=wav,
            sample_rate=sample_rate,
            ttfa_ms=ms,
            ttfa_pcm_ms=ms,
            first_pcm_ready_t=ready_t,
        )

    def _persist_drain(self) -> None:
        try:
            while True:
                self._persist_q.get_nowait()
        except queue.Empty:
            pass

    def _persist_put(self, pcm: np.ndarray | None) -> None:
        self._persist_q.put((self._gen, pcm))

    def stop(self) -> None:
        """中止当前播放；持久路径从不调用全局 sd.stop()。"""
        t0 = time.perf_counter()
        self._stop.set()
        self._gen += 1
        self._persist_need_pcm = False
        self._persist_drain()
        with self._persist_lock:
            self._persist_leftover = np.zeros(0, dtype=np.float32)
        self._persist_play_done.set()
        if not bool(getattr(config, "TTS_PERSISTENT_OUTPUT", False)):
            try:
                sd.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.last_play_timing is not None:
            self.last_play_timing.stop_latency_ms = (time.perf_counter() - t0) * 1000
            self.last_play_timing.interrupted = True

    def _append_tail_pad(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        pad_sec = float(getattr(config, "TTS_PLAY_TAIL_PAD_SEC", 0.18))
        if pad_sec <= 0:
            return audio
        n = int(pad_sec * sample_rate)
        if n <= 0:
            return audio
        return np.concatenate([audio, np.zeros(n, dtype=np.float32)])

    def _play_oneshot_stream(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        speak_t0: float,
        first_pcm_ready_t: float | None = None,
    ) -> PlayTiming:
        """新建 OutputStream，播放缓冲后关闭。全量埋点。"""
        timing = PlayTiming(
            persistent=False,
            stream_reused=False,
            first_pcm_ready_t=first_pcm_ready_t,
        )
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return timing
        audio = self._append_tail_pad(audio, sample_rate)
        timing.samples_expected = int(audio.size)
        timing.expected_play_sec = audio.size / float(sample_rate)
        self._stop.clear()
        pos = 0
        active_underruns = 0
        first_cb_t: float | None = None
        last_write_t = 0.0
        wrote_nonzero = False

        def callback(outdata, frames, _time_info, status) -> None:  # noqa: ANN001
            nonlocal pos, active_underruns, first_cb_t, last_write_t, wrote_nonzero
            if status:
                active_underruns += 1
            if self._stop.is_set():
                outdata.fill(0)
                raise sd.CallbackStop
            end = min(pos + frames, audio.size)
            n = end - pos
            if n > 0:
                outdata[:n, 0] = audio[pos:end]
                if not wrote_nonzero and np.any(audio[pos:end]):
                    wrote_nonzero = True
                    first_cb_t = time.perf_counter()
                last_write_t = time.perf_counter()
            if n < frames:
                outdata[n:].fill(0)
                pos = end
                raise sd.CallbackStop
            pos = end

        kwargs: dict = {
            "samplerate": int(sample_rate),
            "channels": 1,
            "dtype": "float32",
            "callback": callback,
        }
        if self.output_device is not None:
            kwargs["device"] = self.output_device

        t_open0 = time.perf_counter()
        try:
            with sd.OutputStream(**kwargs) as stream:
                timing.stream_open_ms = (time.perf_counter() - t_open0) * 1000
                while stream.active:
                    if self._stop.is_set():
                        timing.interrupted = True
                        break
                    sd.sleep(20)
        except Exception as exc:  # noqa: BLE001
            print(f"[TTS play stream fallback] {exc}")
            timing.stream_open_ms = (time.perf_counter() - t_open0) * 1000
            timing.callback_valid = False
            timing.first_callback_t = None
            timing.ttfa_callback_ms = 0.0
            if self._stop.is_set():
                timing.interrupted = True
                self.last_play_timing = timing
                return timing
            # 阻塞回退：无真实 callback 时间戳 — 保持未知
            play_kw: dict = {"blocking": True}
            if self.output_device is not None:
                play_kw["device"] = self.output_device
            sd.play(audio, sample_rate, **play_kw)
            sd.wait()
            timing.actual_callback_play_sec = timing.expected_play_sec
            timing.samples_written = timing.samples_expected
            self.last_play_timing = timing
            return timing

        t_ret = time.perf_counter()
        timing.active_underruns = active_underruns
        timing.underruns = active_underruns
        timing.samples_written = int(pos)
        timing.actual_callback_play_sec = pos / float(sample_rate)
        if first_cb_t is not None:
            timing.first_callback_t = first_cb_t
            timing.ttfa_callback_ms = (first_cb_t - speak_t0) * 1000
            timing.callback_valid = True
        if last_write_t > 0:
            timing.close_or_drain_ms = (t_ret - last_write_t) * 1000
        if self._stop.is_set():
            timing.interrupted = True
        self.last_play_timing = timing
        return timing

    def _ensure_persist_stream(self, sample_rate: int) -> tuple[float, bool]:
        """返回 (本 utterance 的 open_ms, 是否复用)。"""
        with self._persist_lock:
            if (
                self._persist_stream is not None
                and self._persist_sr == int(sample_rate)
            ):
                return 0.0, True

            old = self._persist_stream
            self._persist_stream = None
            self._persist_sr = None
        if old is not None:
            try:
                old.stop()
                old.close()
            except Exception:  # noqa: BLE001
                pass

        def persist_callback(outdata, frames, _time_info, status) -> None:  # noqa: ANN001
            # 无锁/无 print — 仅队列与普通计数。
            my_gen = self._persist_play_gen
            if status:
                if self._persist_need_pcm and my_gen == self._gen:
                    self._persist_active_underruns += 1
                else:
                    self._persist_idle_callbacks += 1
            if self._stop.is_set() or my_gen != self._gen:
                outdata.fill(0)
                self._persist_need_pcm = False
                self._persist_play_done.set()
                return
            need = int(frames)
            out_pos = 0
            while need > 0:
                if self._persist_leftover.size == 0:
                    try:
                        igen, piece = self._persist_q.get_nowait()
                    except queue.Empty:
                        outdata[out_pos:].fill(0)
                        if self._persist_need_pcm:
                            self._persist_active_underruns += 1
                        else:
                            self._persist_idle_callbacks += 1
                        return
                    if igen != my_gen:
                        continue  # stop 后过期
                    if piece is None:
                        outdata[out_pos:].fill(0)
                        self._persist_need_pcm = False
                        self._persist_play_done.set()
                        return
                    self._persist_leftover = piece
                take = min(need, int(self._persist_leftover.size))
                chunk = self._persist_leftover[:take]
                outdata[out_pos : out_pos + take, 0] = chunk
                if not self._persist_wrote_nonzero and np.any(chunk):
                    self._persist_wrote_nonzero = True
                    t_cb = time.perf_counter()
                    if self._persist_timing is not None:
                        self._persist_timing.first_callback_t = t_cb
                        self._persist_timing.callback_valid = True
                        if self._persist_speak_t0:
                            self._persist_timing.ttfa_callback_ms = (
                                t_cb - self._persist_speak_t0
                            ) * 1000
                self._persist_leftover = self._persist_leftover[take:]
                out_pos += take
                need -= take
                self._persist_samples += take
                self._persist_last_write_t = time.perf_counter()

        kwargs: dict = {
            "samplerate": int(sample_rate),
            "channels": 1,
            "dtype": "float32",
            "callback": persist_callback,
            "blocksize": 0,
        }
        if self.output_device is not None:
            kwargs["device"] = self.output_device
        t0 = time.perf_counter()
        stream = sd.OutputStream(**kwargs)
        stream.start()
        open_ms = (time.perf_counter() - t0) * 1000
        with self._persist_lock:
            self._persist_stream = stream
            self._persist_sr = int(sample_rate)
            self._persist_open_ms = open_ms
            self._persist_first_open = False
        return open_ms, False

    def _begin_persist_play(
        self,
        sample_rate: int,
        *,
        speak_t0: float,
        first_pcm_ready_t: float | None,
        samples_expected: int = 0,
    ) -> PlayTiming:
        """为单 utterance 武装持久流（调用方喂 PCM + sentinel）。"""
        self._stop.clear()
        self._persist_drain()
        with self._persist_lock:
            self._persist_leftover = np.zeros(0, dtype=np.float32)
        open_ms, reused = self._ensure_persist_stream(sample_rate)
        timing = PlayTiming(
            persistent=True,
            stream_reused=reused,
            stream_open_ms=open_ms,
            first_pcm_ready_t=first_pcm_ready_t,
            samples_expected=samples_expected,
        )
        if samples_expected > 0:
            timing.expected_play_sec = samples_expected / float(sample_rate)
        self._persist_play_done.clear()
        self._persist_timing = timing
        self._persist_speak_t0 = speak_t0
        self._persist_play_gen = self._gen
        self._persist_need_pcm = True
        self._persist_wrote_nonzero = False
        self._persist_samples = 0
        self._persist_active_underruns = 0
        self._persist_idle_callbacks = 0
        self._persist_last_write_t = 0.0
        return timing

    def _finish_persist_play(self, timing: PlayTiming, sample_rate: int) -> PlayTiming:
        while not self._persist_play_done.wait(timeout=0.05):
            if self._stop.is_set():
                break
        t_ret = time.perf_counter()
        timing.active_underruns = int(self._persist_active_underruns)
        timing.underruns = timing.active_underruns
        timing.idle_callbacks = int(self._persist_idle_callbacks)
        timing.samples_written = int(self._persist_samples)
        timing.actual_callback_play_sec = self._persist_samples / float(sample_rate)
        if self._persist_last_write_t > 0:
            timing.close_or_drain_ms = (t_ret - self._persist_last_write_t) * 1000
        if self._stop.is_set():
            timing.interrupted = True
        # 若无非零写入，勿伪造 callback 时间
        if not timing.callback_valid:
            timing.first_callback_t = None
            timing.ttfa_callback_ms = 0.0
        self.last_play_timing = timing
        return timing

    def _play_persistent(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        speak_t0: float,
        first_pcm_ready_t: float | None = None,
    ) -> PlayTiming:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return PlayTiming(persistent=True, first_pcm_ready_t=first_pcm_ready_t)
        audio = self._append_tail_pad(audio, sample_rate)
        pcm_ready = first_pcm_ready_t if first_pcm_ready_t is not None else speak_t0
        timing = self._begin_persist_play(
            sample_rate,
            speak_t0=speak_t0,
            first_pcm_ready_t=pcm_ready,
            samples_expected=int(audio.size),
        )
        self._persist_put(audio)
        self._persist_put(None)
        return self._finish_persist_play(timing, sample_rate)

    def play_array(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        speak_t0: float | None = None,
        first_pcm_ready_t: float | None = None,
    ) -> float:
        """可中断播放；返回实际 callback 播放秒数。"""
        if audio.size == 0:
            return 0.0
        t0 = speak_t0 if speak_t0 is not None else time.perf_counter()
        if bool(getattr(config, "TTS_PERSISTENT_OUTPUT", False)):
            timing = self._play_persistent(
                audio,
                sample_rate,
                speak_t0=t0,
                first_pcm_ready_t=first_pcm_ready_t,
            )
        else:
            timing = self._play_oneshot_stream(
                audio,
                sample_rate,
                speak_t0=t0,
                first_pcm_ready_t=first_pcm_ready_t,
            )
        return timing.actual_callback_play_sec or timing.expected_play_sec

    def play_file(self, path: Path) -> float:
        data, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return self.play_array(data, sr)

    def _synth_clause(
        self, voice: PiperVoice, clause: str
    ) -> tuple[np.ndarray, int]:
        chunks: list[np.ndarray] = []
        sample_rate = 22050
        for chunk in voice.synthesize(clause):
            sample_rate = int(chunk.sample_rate)
            audio = np.asarray(chunk.audio_float_array, dtype=np.float32).reshape(-1)
            if audio.size:
                chunks.append(audio)
        if not chunks:
            return np.zeros(0, dtype=np.float32), sample_rate
        return np.concatenate(chunks), sample_rate

    def _speak_clauses(
        self, text: str, lang: str, save_wav: bool = False
    ) -> tuple[TtsResult, float]:
        """合成第 1 句 → 开播；后续句合成与播放重叠（真实 TTFA 优化）。"""
        text = (text or "").strip()
        if not text:
            return TtsResult(None, 0.0, ""), 0.0

        clauses = split_tts_clauses(text)
        speak_t0 = time.perf_counter()
        if len(clauses) <= 1:
            syn = self.synthesize(text, lang, save_wav=save_wav)
            syn.ttfa_pcm_ms = syn.ttfa_ms
            play_sec = 0.0
            if syn.audio is not None and syn.audio.size:
                play_sec = self.play_array(
                    syn.audio,
                    syn.sample_rate,
                    speak_t0=speak_t0,
                    first_pcm_ready_t=syn.first_pcm_ready_t,
                )
                syn.play_timing = self.last_play_timing
            return syn, play_sec

        self._stop.clear()
        voice, voice_name = self._load(lang)
        pad_sec = float(getattr(config, "TTS_PLAY_TAIL_PAD_SEC", 0.18))
        pcm_q: queue.Queue[np.ndarray | None] = queue.Queue()
        chunks_all: list[np.ndarray] = []
        sample_rate_box = [22050]
        first_ready = threading.Event()
        ttfa_pcm_box = [0.0]
        first_pcm_t_box: list[float | None] = [None]
        prod_err: list[BaseException] = []
        expected_samples = [0]

        def producer() -> None:
            try:
                for i, clause in enumerate(clauses):
                    if self._stop.is_set():
                        break
                    audio, sr = self._synth_clause(voice, clause)
                    sample_rate_box[0] = sr
                    if audio.size == 0:
                        continue
                    chunks_all.append(audio)
                    expected_samples[0] += int(audio.size)
                    pcm_q.put(audio)
                    if i == 0 and not first_ready.is_set():
                        first_pcm_t_box[0] = time.perf_counter()
                        ttfa_pcm_box[0] = (first_pcm_t_box[0] - speak_t0) * 1000
                        first_ready.set()
                if pad_sec > 0 and not self._stop.is_set():
                    n = int(pad_sec * sample_rate_box[0])
                    if n > 0:
                        pad = np.zeros(n, dtype=np.float32)
                        chunks_all.append(pad)
                        expected_samples[0] += n
                        pcm_q.put(pad)
            except BaseException as exc:  # noqa: BLE001
                prod_err.append(exc)
            finally:
                if not first_ready.is_set():
                    first_pcm_t_box[0] = time.perf_counter()
                    ttfa_pcm_box[0] = (first_pcm_t_box[0] - speak_t0) * 1000
                    first_ready.set()
                pcm_q.put(None)

        th = threading.Thread(target=producer, daemon=True, name="tts-clauses")
        th.start()
        if not first_ready.wait(timeout=60):
            self._stop.set()
            th.join(timeout=2)
            return (
                TtsResult(
                    None, 0.0, voice_name, ttfa_ms=60000.0, ttfa_pcm_ms=60000.0
                ),
                0.0,
            )

        if bool(getattr(config, "TTS_PERSISTENT_OUTPUT", False)):
            first = None
            saw_end = False
            while True:
                item = pcm_q.get()
                if item is None:
                    saw_end = True
                    break
                first = item
                break
            timing = self._begin_persist_play(
                int(sample_rate_box[0]),
                speak_t0=speak_t0,
                first_pcm_ready_t=first_pcm_t_box[0],
                samples_expected=0,
            )
            feed_gen = self._gen
            if first is not None:
                self._persist_q.put((feed_gen, first))

            def feeder() -> None:
                try:
                    if not saw_end:
                        while True:
                            item = pcm_q.get()
                            if item is None:
                                break
                            if feed_gen != self._gen:
                                break
                            self._persist_q.put((feed_gen, item))
                finally:
                    if feed_gen == self._gen:
                        self._persist_q.put((feed_gen, None))

            ft = threading.Thread(target=feeder, daemon=True)
            ft.start()
            timing = self._finish_persist_play(timing, int(sample_rate_box[0]))
            ft.join(timeout=30)
            th.join(timeout=30)
            timing.samples_expected = int(expected_samples[0])
            timing.expected_play_sec = expected_samples[0] / float(sample_rate_box[0])
            syn_ms = (time.perf_counter() - speak_t0) * 1000
            wav = np.concatenate(chunks_all) if chunks_all else None
            path = None
            if save_wav and wav is not None and wav.size:
                path = self.out_dir / f"utt_{int(time.time() * 1000)}.wav"
                sf.write(str(path), wav, int(sample_rate_box[0]))
            return (
                TtsResult(
                    path,
                    syn_ms,
                    voice_name,
                    audio=wav,
                    sample_rate=int(sample_rate_box[0]),
                    ttfa_ms=float(ttfa_pcm_box[0]),
                    ttfa_pcm_ms=float(ttfa_pcm_box[0]),
                    first_pcm_ready_t=first_pcm_t_box[0],
                    play_timing=timing,
                ),
                timing.actual_callback_play_sec,
            )

        # 非持久：实时队列 → 新建 OutputStream
        leftover = np.zeros(0, dtype=np.float32)
        pos_played = 0
        sr = int(sample_rate_box[0])
        active_underruns = 0
        first_cb_t: float | None = None
        last_write_t = 0.0
        wrote_nonzero = False

        def callback(outdata, frames, _time_info, status) -> None:  # noqa: ANN001
            nonlocal leftover, pos_played, active_underruns, first_cb_t, last_write_t, wrote_nonzero
            if status:
                active_underruns += 1
            if self._stop.is_set():
                outdata.fill(0)
                raise sd.CallbackStop
            need = int(frames)
            out_pos = 0
            while need > 0:
                if leftover.size == 0:
                    try:
                        piece = pcm_q.get_nowait()
                    except queue.Empty:
                        outdata[out_pos:].fill(0)
                        active_underruns += 1
                        return
                    if piece is None:
                        outdata[out_pos:].fill(0)
                        raise sd.CallbackStop
                    leftover = piece
                take = min(need, int(leftover.size))
                chunk = leftover[:take]
                outdata[out_pos : out_pos + take, 0] = chunk
                if not wrote_nonzero and np.any(chunk):
                    wrote_nonzero = True
                    first_cb_t = time.perf_counter()
                leftover = leftover[take:]
                out_pos += take
                need -= take
                pos_played += take
                last_write_t = time.perf_counter()

        kwargs: dict = {
            "samplerate": sr,
            "channels": 1,
            "dtype": "float32",
            "callback": callback,
        }
        if self.output_device is not None:
            kwargs["device"] = self.output_device

        timing = PlayTiming(
            persistent=False, first_pcm_ready_t=first_pcm_t_box[0]
        )
        t_open0 = time.perf_counter()
        try:
            with sd.OutputStream(**kwargs) as stream:
                timing.stream_open_ms = (time.perf_counter() - t_open0) * 1000
                while stream.active:
                    if self._stop.is_set():
                        timing.interrupted = True
                        break
                    sd.sleep(20)
        except Exception as exc:  # noqa: BLE001
            print(f"[TTS clause-play fallback] {exc}")
            th.join(timeout=30)
            wav = np.concatenate(chunks_all) if chunks_all else np.zeros(0, np.float32)
            play_sec = 0.0
            if wav.size and not self._stop.is_set():
                play_sec = self.play_array(
                    wav,
                    sr,
                    speak_t0=speak_t0,
                    first_pcm_ready_t=first_pcm_t_box[0],
                )
            syn_ms = (time.perf_counter() - speak_t0) * 1000
            return (
                TtsResult(
                    None,
                    syn_ms,
                    voice_name,
                    audio=wav if wav.size else None,
                    sample_rate=sr,
                    ttfa_ms=ttfa_pcm_box[0] or syn_ms,
                    ttfa_pcm_ms=ttfa_pcm_box[0] or syn_ms,
                    first_pcm_ready_t=first_pcm_t_box[0],
                    play_timing=self.last_play_timing,
                ),
                play_sec,
            )

        th.join(timeout=30)
        t_ret = time.perf_counter()
        timing.active_underruns = active_underruns
        timing.underruns = active_underruns
        timing.samples_written = int(pos_played)
        timing.samples_expected = int(expected_samples[0])
        timing.expected_play_sec = expected_samples[0] / float(sr) if sr else 0.0
        timing.actual_callback_play_sec = pos_played / float(sr) if sr else 0.0
        if first_cb_t is not None:
            timing.first_callback_t = first_cb_t
            timing.ttfa_callback_ms = (first_cb_t - speak_t0) * 1000
            timing.callback_valid = True
        if last_write_t > 0:
            timing.close_or_drain_ms = (t_ret - last_write_t) * 1000
        self.last_play_timing = timing
        if prod_err:
            print(f"[TTS clause synth error] {prod_err[0]}")
        syn_ms = (time.perf_counter() - speak_t0) * 1000
        wav = np.concatenate(chunks_all) if chunks_all else None
        path = None
        if save_wav and wav is not None and wav.size:
            path = self.out_dir / f"utt_{int(time.time() * 1000)}.wav"
            sf.write(str(path), wav, sr)
        return (
            TtsResult(
                path,
                syn_ms,
                voice_name,
                audio=wav,
                sample_rate=sr,
                ttfa_ms=float(ttfa_pcm_box[0]),
                ttfa_pcm_ms=float(ttfa_pcm_box[0]),
                first_pcm_ready_t=first_pcm_t_box[0],
                play_timing=timing,
            ),
            timing.actual_callback_play_sec,
        )

    def speak(
        self, text: str, lang: str, save_wav: bool = False
    ) -> tuple[TtsResult, float]:
        with self._io_lock:
            if bool(getattr(config, "TTS_STREAM_PLAY", True)):
                return self._speak_clauses(text, lang, save_wav=save_wav)
            speak_t0 = time.perf_counter()
            syn = self.synthesize(text, lang, save_wav=save_wav)
            syn.ttfa_pcm_ms = syn.ttfa_ms
            play_sec = 0.0
            if syn.audio is not None and syn.audio.size:
                play_sec = self.play_array(
                    syn.audio,
                    syn.sample_rate,
                    speak_t0=speak_t0,
                    first_pcm_ready_t=syn.first_pcm_ready_t,
                )
                syn.play_timing = self.last_play_timing
            elif syn.path and syn.path.exists():
                play_sec = self.play_file(syn.path)
                syn.play_timing = self.last_play_timing
            return syn, play_sec
