# 可用基线：final_only（冻住）

> 贴给其他模型 / 后人时 **只以本文为准**。  
> 旧笔记里若仍出现「强挂起含 点/的/多少」或 REINA 中途 WRITE，均为 **历史拟议，已弃用**。

## 目标与硬件

- 本地中↔英半双工通话翻译（说完再播）。
- Ryzen 7 4800H + 32GB，无 NVIDIA，全 CPU。

## 流水线

Silero VAD → Sherpa Online Paraformer（流式 ASR，字幕草稿）→ **句尾整句** CT2 Opus-MT → Piper TTS。

- 开关：`MID_MT_ENABLED=False`（`app/config.py`）
- 启动：`python run.py --mode vad`（麦 zh→en；**同时只能一个实例**）
- 环回对端：`python run.py --mode loopback`（系统播放声 en→zh）  
  双通道：`python run.py --mode duplex`（麦+环回并行；TTS/对端请分设备，见下）  
  列环回：`python run.py --list-loopback`
- 仿真：`python run.py --mode dualsim --file path\to.wav --no-tts`  
  或 `python scripts/bench_dualsim.py --wav path\to.wav --tag ...`

### 环回 / 双通道注意

- 依赖：`soundcard`（WASAPI loopback）；`sounddevice` **无** loopback。
- 默认环回 = 当前默认扬声器的 loopback；可用 `LOOPBACK_DEVICE_NAME` 子串指定。
- **TTS 与对端声必须分设备**：`TTS_OUTPUT_DEVICE_NAME`（播放）≠ 环回所跟的扬声器。  
  列设备：`python run.py --list-output` / `--list-loopback`。  
  `LOOPBACK_MUTE_ON_TTS=True` 仅作缓兵。
- 实机模式带单实例锁（`cache/run.lock`），防双开串音。

## 断句策略（落地）

| 规则 | 现状 |
|------|------|
| 中途 MT / 开放 hang 黑名单 | **已删**，不要加回 |
| 收句 | 声学静音为主；非白名单立刻 cut |
| 白名单（可数） | 仅 `星期`（总长≤8）或 `是星`（总长≤4）；**不含「点」** |
| 补全窗口 | `ENDPOINT_COMPLETION_WINDOW_SEC`（默认 0.85s） |
| 同句轮次 | `ENDPOINT_COMPLETION_MAX_ROUNDS=1`（synth A/B 与 2 平手，选更短最坏等待） |
| 强切 | **也译**；`should_skip_final_mt` 恒 False |
| TTS | 句尾升 version 作废旧任务；半双工 mute |

### 补全窗口起算（写死）

**静音后叠加，不是静音预算内切分。**

1. Silero 静音满 `VAD_SILENCE_SEC`（默认 0.8s）→ `on_vad_candidate`
2. 短暂 partial sync（`ENDPOINT_PARTIAL_SYNC_MS`，默认 100ms）
3. 若文本仍为星期白名单未说完 → 从 **此刻** 起再等 `ENDPOINT_COMPLETION_WINDOW_SEC`

因此「真停在星期」时，额外等待 ≈ sync + 窗口，与静音 **相加**，不是「0.85 − 0.8 = 0.05」。

实现：`EndpointArbiter._judge_text` 内 `self._completion_deadline = now + win`。

## 配置标注

| 配置 | 值 / 状态 |
|------|-----------|
| `MID_MT_ENABLED` | `False`（生效） |
| `VAD_SILENCE_SEC` | 生效 |
| `ENDPOINT_COMPLETION_WINDOW_SEC` | 生效（静音后叠加） |
| `ENDPOINT_COMPLETION_MAX_ROUNDS` | 生效 |
| `VAD_FORCE_END_UNCHANGED` / `_COMPLETE` | **仍生效**（unchanged 强切；不是 hang 名单） |
| `VAD_HANG_EXTRA_SILENCE_SEC` | **遗留**，现为 `0.0`，勿当断句手段 |

## 显式假设（最易被后人误解）

**断句简化把质量压力转移到「final ASR 全文 → 整句 MT」。**  
ASR 少末字会直接变成怪译，用户易误判为「断句又坏了」。  
因此工程优先 **ASR 末字完整性**；Opus-MT 语义拧是 **独立方向**，不要同一版混改。

### ASR 末字（已做）

- 收句瞬间仍把当前 chunk 写入 `buf`（勿只看 `endpoint.in_speech`，否则丢尾块）
- `ONLINE_TAIL_PAD_SEC=1.0` + finalize 前 `stream.set_option("is_final","1")`（CIF flush / 短尾块）
- `ASR_FINAL_2PASS` + 静音≥`ASR_SPECULATE_SILENCE_SEC` 投机定稿（CUT 优先取；超时 drain 同 job，勿整段重跑）
- `prefer_final_asr(partial, final)`：二者互为前缀时取更长
- 手感：`VAD_SILENCE_SEC=0.45`（paced A/B：优于 0.60；0.35 气口更险），`VAD_FORCE_END_UNCHANGED_COMPLETE=2`
- 体感尺子：`python scripts/bench_paced.py --spec on|off [--silence 0.45] [--wav ...]`
- 主指标：`latency_perceived_ms`（停说→译文）；**不要**再用 dualsim `utt_wall` 当体感

### 体感 A/B（2026-11-03，`docs/paced_bench.jsonl`）

| 条件 | mean perceived（约） | 说明 |
|------|---------------------|------|
| 投机关 + sil 0.60 | ~1.0–1.1s | 对照 |
| 投机开 + sil 0.60 | ~0.64–0.72s（命中句） | ASR 藏进静音 |
| 投机开 + sil 0.45 | ~0.49–0.64s | **当前默认** |
| 投机开 + sil 0.35 | 个别更低，气口风险↑ | 不默认 |

## MT 质量（独立线）

- 评测集：`data/mt_call_eval.jsonl`；种子：`data/mt_call_seed.jsonl`
- 对比：`python scripts/bench_mt_call_quality.py --lora models/opus-mt-zh-en-lora-call-ct2`
- 前缀 LoRA（`opus-mt-zh-en-lora-ct2`）**不接入**
- **已接入 zh→en**：`models/opus-mt-zh-en-lora-call-ct2`（整句通话域 LoRA，seed≈84，6 epoch）  
  + `app/mt_postedit.py` 兜底（回消息 / N面 / 听不清 / 天气怎么样 / 翻译慢）  
  回退：`OPUS_ZH_EN_CT2` → `OPUS_ZH_EN_CT2_BASE`
- 离线验收：`python scripts/accept_offline.py`
- 抽检清单（合成 wav，不必对着麦）：`python scripts/accept_audio_checklist.py`

## 已知瑕疵（非断句回退）

- 实机仍可能少末字（麦噪/轻声尾音）→ 继续观察
- 其余口语拧译：继续往评测集加句，能规则就规则，否则再考虑通话域 LoRA
- 停在「星期」强切后可能 *It's a week* → 已接受（强切也译）
- 「星期」偶被听成「新区/新期」→ ASR

## 用网上/本地音频测（不必反复对着麦）

1. 下载任意中文说话音频（播客片段、公开朗读等）。
2. 转成 16kHz mono wav，例如：  
   `ffmpeg -i in.mp3 -ac 1 -ar 16000 cache\test.wav`
3. 仿真双轨：  
   `python run.py --mode dualsim --file cache\test.wav --no-tts`  
   或 `python scripts\bench_dualsim.py --wav cache\test.wav --tag dualsim-web --note "..."`
4. 星期气口 A/B 脚本（自合成）：`python scripts\ab_completion_rounds.py`
5. 停→译手感（实时节奏喂块）：`python scripts\bench_latency_knobs.py`

注意：网上下载的是「成品录音」，气口/噪声分布和你的实机麦不同；**断句手感仍以实机抽检为准**，dualsim 适合回归与对比参数。

## 回归

```text
python -m pytest tests/test_weekday_whitelist.py tests/test_endpoint_regression.py -q
```
