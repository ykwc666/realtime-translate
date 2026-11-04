# realtime-translate（中英通话级本地链路）

本机无 NVIDIA，当前按 **CPU** 部署。资源都在 `D:\xuanjian\realtime-translate`，避免占满 C 盘。

## 已下载

| 组件 | 来源 | 路径 |
|------|------|------|
| 流式 ASR Paraformer | ModelScope | `cache\modelscope\models\iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online` |
| VAD fsmn-vad | ModelScope | `cache\modelscope\models\iic--speech_fsmn_vad_zh-cn-16k-common-pytorch` |
| Opus-MT zh→en | hf-mirror | `models\opus-mt-zh-en` |
| Opus-MT en→zh | hf-mirror | `models\opus-mt-en-zh` |
| TTS Piper 中/英 | hf-mirror | `models\piper\zh_CN-huayan-medium.onnx` / `en_US-lessac-medium.onnx` |
| Opus-MT CT2 int8 | 本地转换 | `models\opus-mt-*-ct2` |
| SenseVoiceSmall | ModelScope | `cache\modelscope\...\SenseVoiceSmall` |
| SenseVoice int8 ONNX (sherpa) | hf-mirror | `models\sherpa-sensevoice\` |
| Silero VAD onnx | hf-mirror | `models\silero_vad.onnx` |

## 环境

```powershell
cd D:\xuanjian\realtime-translate
.\.venv\Scripts\Activate.ps1
```

国内源约定：

- pip：清华 `https://pypi.tuna.tsinghua.edu.cn/simple`
- FunASR：ModelScope（`hub="ms"`）
- Opus-MT：`https://hf-mirror.com`（脚本会跳过 xet / tf / rust 文件）

## 重新下载

```powershell
# FunASR（ModelScope，已缓存则秒过）
$env:MODELSCOPE_CACHE = "$PWD\cache\modelscope"
.\.venv\Scripts\python.exe scripts\download_models.py

# 仅 Opus-MT
.\.venv\Scripts\python.exe scripts\download_opus_mt.py

# 仅 Piper 离线 TTS
.\.venv\Scripts\python.exe scripts\download_piper_voices.py

# Opus-MT → CTranslate2 int8
.\.venv\Scripts\python.exe scripts\convert_opus_ct2.py
```

## 测速 / 优化记录

每次优化后跑一遍并自动记账：

```powershell
python scripts/bench.py --tag sensevoice-ct2 --note "说明这次改了什么"
```

历史见 `docs/OPTIMIZATION_LOG.md` 与 `docs/bench_history.jsonl`。

**当前冻住基线**：`docs/BASELINE_FINAL_ONLY.md`（final_only，句尾整句 MT；勿按旧 REINA/hang 名单理解）。

默认：`ASR=sherpa_paraformer_online`，`MT=ct2`，`TTS=Piper`（见 `app/config.py`）。

## 运行

```powershell
cd D:\xuanjian\realtime-translate
.\.venv\Scripts\Activate.ps1

# 半双工：说完按 Enter
python run.py --mode ptt

# 实机 VAD（句尾整句译；同时只开一个进程）
python run.py --mode vad

# 离线处理 wav（整段）
python run.py --mode file --file path\to\audio.wav

# 双轨仿真（网上/本地 wav → 16k mono，不必反复对着麦说）
python run.py --mode dualsim --file path\to\audio.wav --no-tts
python scripts\bench_dualsim.py --wav path\to\audio.wav --tag dualsim-xxx --note "说明"
python scripts\ab_completion_rounds.py

# 只识别+翻译，不播报
python run.py --mode ptt --no-tts
```

## 说明

- TTS 用 **Piper 离线合成**，不联网。
- 半双工：播报时会静音麦克风，减轻回声。
- 仿真测断句/收句：把音频转成 **16kHz mono wav** 后跑 `dualsim`（可用 ffmpeg）。
- **基线说明**：`docs/BASELINE_FINAL_ONLY.md`
- **待优化清单**：`docs/OPTIMIZATION_ROADMAP.md`（其中 REINA 中途段为历史，以 BASELINE 为准）
