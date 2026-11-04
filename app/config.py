from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
CACHE_DIR = ROOT / "cache"
CACHE_MS = CACHE_DIR / "modelscope"
CACHE_HF = CACHE_DIR / "huggingface"

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"

# asr: sherpa_paraformer_online（默认）| sherpa_sensevoice（回退）
ASR_BACKEND = "sherpa_paraformer_online"
SHERPA_SENSEVOICE_DIR = MODELS_DIR / "sherpa-sensevoice"
SHERPA_SENSEVOICE_MODEL = SHERPA_SENSEVOICE_DIR / "model.int8.onnx"
SHERPA_SENSEVOICE_TOKENS = SHERPA_SENSEVOICE_DIR / "tokens.txt"
SHERPA_PF_ONLINE_DIR = MODELS_DIR / "sherpa-paraformer-online"
SHERPA_PF_ONLINE_ENCODER = SHERPA_PF_ONLINE_DIR / "encoder.int8.onnx"
SHERPA_PF_ONLINE_DECODER = SHERPA_PF_ONLINE_DIR / "decoder.int8.onnx"
SHERPA_PF_ONLINE_TOKENS = SHERPA_PF_ONLINE_DIR / "tokens.txt"
# Online Paraformer 句尾右上下文：官方 C++ mic 示例用 ~1.0s；配合 finalize 时 is_final
ONLINE_TAIL_PAD_SEC = 1.0
SILERO_VAD_ONNX = MODELS_DIR / "silero_vad.onnx"
# vad: silero | energy
VAD_BACKEND = "silero"

# mt: transformers | ct2
MT_BACKEND = "ct2"
OPUS_ZH_EN = MODELS_DIR / "opus-mt-zh-en"
OPUS_EN_ZH = MODELS_DIR / "opus-mt-en-zh"
# zh→en：通话域 LoRA CT2（data/mt_call_seed）；回退可改回 opus-mt-zh-en-ct2
OPUS_ZH_EN_CT2 = MODELS_DIR / "opus-mt-zh-en-lora-call-ct2"
OPUS_ZH_EN_CT2_BASE = MODELS_DIR / "opus-mt-zh-en-ct2"
OPUS_EN_ZH_CT2 = MODELS_DIR / "opus-mt-en-zh-ct2"

# 线程：4800H 上避免默认吃满抢核
ASR_THREADS = 6
MT_THREADS = 4
TTS_THREADS = 2

# VAD：句尾静音多久算说完（秒）。paced A/B：0.45 优于 0.60；0.35 气口更险，不默认
VAD_SILENCE_SEC = 0.45
# 最短有效语音（秒），过滤误触发
MIN_SPEECH_SEC = 0.55
# Silero 语音概率阈值（越高越不敏；环境噪声易误触发时再抬）
VAD_SILERO_THRESHOLD = 0.85
# ASR 连续 unchanged 次数后强制收句（防 VAD 粘在噪声上）
VAD_FORCE_END_UNCHANGED = 6
# 文本已像说完（非挂起）时，更早强制收句（次数）；减轻「会议」说完还空转
# 已有字时更早强切（×partial 间隔）；3→2 减轻短句「粘住」
VAD_FORCE_END_UNCHANGED_COMPLETE = 2
# 「星期」未说完时 VAD 不落静音的强切上限（勿再 +3→9，实麦会空等 ~9s）
VAD_FORCE_END_UNCHANGED_WEEKDAY = 4
# 句尾 SenseVoice 二遍：流式出字快，离线抬识别（对标云端差距的本地刀）
ASR_FINAL_2PASS = True
# 静音达到该秒数时后台投机定稿（独立 stream，CUT 时优先取）
ASR_SPECULATE_ENABLED = True
ASR_SPECULATE_SILENCE_SEC = 0.25
ASR_SPECULATE_WAIT_SEC = 0.55  # from spec start; CUT waits only the remainder
# 快照仍有效时，额外允许等投机结果算完（避免超时后整段重跑 = ASR 付两遍）
ASR_SPECULATE_LATE_WAIT_SEC = 0.80
# 提前量不足该秒数就不等投机（强切/极短静音时投机不划算）
ASR_SPECULATE_MIN_HEADSTART_SEC = 0.15
# 句尾有效字数低于此则跳过 MT（挡「就」类噪声碎句）
MIN_FINAL_ASR_CHARS = 2
# 单句最长秒数，超时强制收句
VAD_MAX_UTT_SEC = 15.0
# 开说后若一直无 ASR 字，按噪声早弃（秒）——避免空句卡满 MAX_UTT
VAD_EMPTY_ABORT_SEC = 2.0
# 遗留：曾用加长静音做 hang 断句，已弃用；保持 0，勿再当手段
VAD_HANG_EXTRA_SILENCE_SEC = 0.0
# 候选端点后等待更新 partial 的上限（毫秒）；投机定稿后可压低
ENDPOINT_PARTIAL_SYNC_MS = 20
# 星期白名单补全窗口（秒）：在静音候选 + sync 之后从 now 起算（与静音叠加，不是 0.85−0.8）
# 详见 docs/BASELINE_FINAL_ONLY.md
ENDPOINT_COMPLETION_WINDOW_SEC = 0.85
# A/B（synth dualsim）：1 与 2 得分相同 → 选 1，缩短真停在「星期」时的最坏等待
ENDPOINT_COMPLETION_MAX_ROUNDS = 1

# 能量门限（自适应噪声地板之上；energy 后端用）
VAD_ENERGY_THRESHOLD = 0.015

# Silero 开说额外 RMS 门槛（抑制风扇/底噪误触发；说话通常 ≥0.03）
VAD_SPEECH_RMS_MIN = 0.035

# 播放 TTS 后继续静音麦（秒），盖住扬声器尾音/房间混响，减轻回授
PLAYBACK_MUTE_PAD_SEC = 0.50
# WASAPI 环回（对端播放声）：空=默认扬声器的 loopback；可填设备名子串
LOOPBACK_DEVICE_NAME = ""
# duplex 时我方 TTS 是否静音环回采集（强烈建议 True，否则自听翻译）
LOOPBACK_MUTE_ON_TTS = True
# TTS 输出设备名子串（空=系统默认）。与 LOOPBACK 分设备可免自听，如 VB-CABLE
TTS_OUTPUT_DEVICE_NAME = ""
# 播报波形末尾补静音（秒），避免 Windows 上 sd.play 提前关流切掉尾音节（Tuesday 的 -day）
TTS_PLAY_TAIL_PAD_SEC = 0.18
# 单实例锁（cache/run.lock）；多开会串音/双译
SINGLE_INSTANCE_LOCK = True

# 双轨 partial 间隔：固定 0.75（自适应实测 wall↑，已关）
PARTIAL_INTERVAL_SEC = 0.75
PARTIAL_ADAPTIVE = False  # legacy；保持 False
PARTIAL_INTERVAL_START_SEC = 0.55
PARTIAL_INTERVAL_STABLE_SEC = 0.75
PARTIAL_ADAPT_AFTER_SEC = 0.90

# SenseVoice 回退路径：重叠窗增量（Online Paraformer 不用）
ASR_INCREMENTAL = True
ASR_OVERLAP_SEC = 0.50
# 稳定前缀需连续一致次数
STABLE_AGREE = 2
# 至少稳定多少字才进入待译缓冲
MIN_COMMIT_CHARS = 2
# 待译中文累计到多少字（或遇句号）才真正调用 MT（legacy；精简版句尾整句不用）
MIN_MT_CHARS = 6
# MT 门控 legacy（精简版中途关闭，仅句尾 force）
MT_POLICY = "reina"
MT_INFO_MIN = 8.0
MT_PENDING_HARD_CHARS = 22
MT_PENDING_MAX_WAIT_SEC = 2.0
# 中途预译：精简版默认关（短句一律等句尾整句）
MID_MT_ENABLED = False

# 译文累计到多少字才中途播报（legacy）
MIN_SPEAK_CHARS = 12


# 异步 TTS 队列
TTS_ASYNC = True

# 离线 Piper 音色（models/piper）
PIPER_DIR = MODELS_DIR / "piper"
PIPER_VOICE_ZH = PIPER_DIR / "zh_CN-huayan-medium.onnx"
PIPER_VOICE_EN = PIPER_DIR / "en_US-lessac-medium.onnx"

DEVICE = "cpu"


def setup_env() -> None:
    os.environ.setdefault("MODELSCOPE_CACHE", str(CACHE_MS))
    os.environ.setdefault("HF_HOME", str(CACHE_HF))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(CACHE_HF / "hub"))
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("MODELSCOPE_DOMAIN", "www.modelscope.cn")
