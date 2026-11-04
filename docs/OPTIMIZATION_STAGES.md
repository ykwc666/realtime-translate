# 优化阶段复盘（详细）

> 本文按时间线汇总本机优化做了什么、测到什么、最终采用/回退了什么。  
> **数字一律来自** `docs/OPTIMIZATION_LOG.md`、`docs/bench_history.jsonl` 及对应 bench 脚本输出；未跑通或未写入日志的结论不写进「结果」列。  
> 机器：AMD Ryzen 7 4800H，32GB，无 NVIDIA（Radeon 核显 Renoir），Windows。  
> 主测音频：FunASR 示例 `asr_example.wav`（约 5.55s 中文）。  
> 清单与待办见 `docs/OPTIMIZATION_ROADMAP.md`；原始追加表见 `docs/OPTIMIZATION_LOG.md`。

### 指标怎么读

| 指标 | 含义 | 测法 |
|------|------|------|
| ASR / MT / TTS / E2E（中位 ms） | 整段 wav 一次跑通各段耗时 | `scripts/bench.py` |
| `first_partial_ms` | 本句开说后到**首次** partial ASR 出字 | `scripts/bench_dualsim.py` |
| `first_stable_mt_ms` | 开说后到**首次**稳定前缀触发 MT | 同上 |
| `utt_wall_ms` | 开说到句尾收尾完成（含 VAD 静音等待、final ASR/MT 等） | 同上 |
| `asr_ms_sum` | 本句所有 ASR 调用耗时之和（含 partial + final） | 同上 |
| `mt_triggers` | 本句实际调用 MT 次数 | 同上 |

说明：

- dualsim 默认 **不播 TTS**（`play=False`），故多数记录 `tts_ms_sum=0`；TTS 延迟另用 Piper A/B 或整段 bench。
- `utt_wall` **包含** VAD 静音门槛（配置 `VAD_SILENCE_SEC=0.35`），不是「停说瞬间到出字」的纯模型延迟。
- 同标签若跑过多次，以 `bench_history.jsonl` 中对应 `tag` 那一次为准。

---

## 阶段 0：起点基线（整段流水线）

**目标：** 量出「本地 ZH↔EN 通话翻译」最初有多慢。

**配置：** FunASR 在线 Paraformer（`paraformer_stream`）+ transformers Opus-MT；当时 TTS 未计入或为 0。

**结果（tag=`baseline-paraformer-transformers`，2026-11-02 19:00:36）：**

| ASR 中位 | MT 中位 | TTS 中位 | E2E 中位 |
|----------|---------|----------|----------|
| 2429.9 ms | 836.7 ms | 0 | 3332.7 ms |

**结论：** 瓶颈同时在 ASR（~2.4s）与 MT（~0.8s）；整段约 **3.3s**，无法做实时通话体感。

---

## 阶段 1：换推理栈（仍是「说完再整段处理」）

本阶段不改双轨，只换模型与运行时，用 `bench.py` 看整段中位延迟。

### 1.1 MT：transformers Opus → CTranslate2 int8

**改动：** `MT_BACKEND=ct2`，模型目录 `models/opus-mt-*-ct2`。

**结果（tag=`paraformer-ct2`）：**

| | ASR | MT | E2E |
|--|-----|-----|-----|
| 基线 | 2430 | 837 | 3333 |
| CT2 后 | 2027 | **80** | **2105** |

**结论：** MT **836.7 → 80.0 ms**（约 10×）；E2E 降到 ~2.1s。ASR 仍是大头。

### 1.2 ASR：Paraformer → SenseVoice（PyTorch / FunASR）

**结果（tag=`sensevoice-ct2`）：**

| ASR | MT | TTS | E2E |
|-----|-----|-----|-----|
| 757.5 | 98.0 | 241.5 | 1097.1 |

**结论：** ASR 降到 ~0.76s；E2E 首次进入约 **1.1s**。已接 Piper，TTS 中位 ~242ms（此轮有合成）。

### 1.3 ASR：SenseVoice → sherpa-onnx int8 ONNX

**改动：** `ASR_BACKEND=sherpa_sensevoice`，模型 `models/sherpa-sensevoice`。

**结果：**

| tag | ASR | MT | TTS | E2E | 备注 |
|-----|-----|-----|-----|-----|------|
| `sherpa-sensevoice-ct2` | 331.4 | 102.8 | **837.9** | 1272.4 | TTS **未预热**，冷启动很重 |
| `sherpa-ct2-tts-warmup` | 459.4 | 206.2 | **400.8** | 1261.7 | 中英 Piper 预热；记录里首轮 TTS 曾到 **254.6 ms** |

**结论：**

- sherpa ONNX 把 ASR 压到约 **330–460 ms**（同机波动受线程/缓存影响）。
- TTS 必须预热；未预热中位可到 ~840ms，预热后可用。
- 至此「整段说完再译」E2E 约 **1.2–1.3s**，仍偏「批处理」而非边说边出。

### 1.4 相对阶段 0 的累计（整段）

| 项 | 基线 | 阶段 1 末（量级） |
|----|------|-------------------|
| ASR | ~2430 ms | ~330–460 ms |
| MT | ~837 ms | ~80–200 ms |
| E2E | ~3333 ms | ~1100–1270 ms |

---

## 阶段 2：双轨架构（边说边出）

**目标：** 说话过程中出草稿 ASR，稳定前缀后增量 MT，句尾再确认；体感指标改为 dualsim 的 first_partial / first_stable_mt / utt_wall。

### 2.1 骨架与计时修正

| tag | partials | first_partial | first_stable_mt | wall | 说明 |
|-----|----------|---------------|-----------------|------|------|
| `dualsim-v1` | 0 | — | — | 359.8 | 计时/触发有 bug，**不可作基线** |
| `dualsim-v2` | 5 | 89.3 | — | 1116.5 | partial 按**音频时间轴**触发；稳定前缀尚未适配累积 ASR |
| `dualsim-v3` | 5 | 88.5 | **239.4** | 1273.9 | 累积 ASR + 稳定前缀生效；开始有「说话中首译」 |

**结论：** 双轨可用后，开说约 **90ms** 内有 partial；最早一次测到首稳译约 **240ms**（此后门控变严，该数字不再代表默认行为）。

### 2.2 Silero VAD + 条件 MT + 异步 TTS

**改动：**

- `VAD_BACKEND=silero`（失败回退能量 VAD），`VAD_SILENCE_SEC=0.35`
- 待译缓冲达 `MIN_MT_CHARS=6` 或句界才调用 MT
- `TTS_ASYNC=True`，`app/tts_queue.py`

**结果（tag=`dualsim-silero-condmt`）：**

| first_partial | first_stable_mt | wall | mt 相关 |
|---------------|-----------------|------|---------|
| 84.8 | **622.9** | **1559.9** | mt_ms_sum=186.9 |

**结论：** 切句更稳、碎译更少，但首稳译和 wall **明显变慢**——条件 MT 会推迟第一次翻译，属预期权衡，不是实现回退。

### 2.3 ASR 增量重叠窗（少重算）

**问题：** 每个 partial 若整段重跑，音频越长 ASR 越贵。对照 `ASR_INCREMENTAL=False`：

| tag | asr_ms_sum | partial 样本序列（节选） | wall |
|-----|------------|--------------------------|------|
| `dualsim-asr-full` | **1261.2** | 12.8k → … → 76.8k，final 67.8k | 1533.0 |
| `dualsim-asr-incr3`（overlap=0.8） | **869.9** | partial 多封顶 25.6k | 1170.9 |
| `dualsim-asr-incr4`（overlap=0.8 + Latin 剥离加强） | 879.5 | 同上量级 | 1160.6 |
| **`dualsim-overlap-0.5`（采用）** | **831.3** | partial 封顶约 20.8k | **1143.9** |

相对 `dualsim-asr-full`：`asr_ms_sum` **1261.2 → 831.3（约 −34%）**。

实现要点（SenseVoice 路径）：`ASR_INCREMENTAL=True`，只识别「上次位置 − overlap」到末尾，再用 `merge_overlap_text` 拼接；对短 Latin 误识前缀做丢弃/剥离。句尾仍整段 `transcribe` 确认。

中间失败过一轮（`dualsim-asr-incr2`）：误识丢弃策略过激，`first_stable_mt=1456`、`wall=1777`、`asr_ms_sum=1541`——已废弃该策略，不采用。

### 2.4 partial 间隔 0.5s A/B → 回退 0.75s

| tag | 间隔 | partials | first_partial | first_stable_mt | wall | mt_triggers |
|-----|------|----------|---------------|-----------------|------|-------------|
| `dualsim-partial-0.5` | 0.5s | **10** | 56.1 | **295.9** | **1521.1** | **6** |
| `dualsim-overlap-0.5` | 0.75s | 6 | 86.8 | 477.5 | 1143.9 | 3 |

**结论：** 0.5s 让首译更快，但 partial/MT 次数上升、译文更碎、**wall 变差** → **默认保持 `PARTIAL_INTERVAL_SEC=0.75`**。

### 2.5 阶段 2 末（SenseVoice 增量双轨）大致水位

以 `dualsim-overlap-0.5` 为代表：

- first_partial ≈ **87 ms**
- first_stable_mt ≈ **478 ms**
- utt_wall ≈ **1144 ms**
- asr_ms_sum ≈ **831 ms**

---

## 阶段 3：可选加速实验（多数否决）

这些实验有独立 tag 与脚本；**未改善主路径则保持 CPU / 原模型**。

### 3.1 Piper：CPU vs DirectML

环境：主 `.venv`（ORT 1.31.1 CPU）vs `.venv-dml`（ORT 1.24.4 + DML）。脚本：`scripts/bench_piper_providers.py`。

| tag | provider | zh_short | zh_mid | en_short | en_mid |
|-----|----------|----------|--------|----------|--------|
| `piper-cpu-ab` | CPU | 52.8 | **371.2** | 55.9 | **151.1** |
| `piper-dml-ab` | DirectML | 292.7 | **780.7** | 272.2 | **538.4** |

**结论：弃用 DirectML。** Renoir 核显上 Piper 约慢 2–5×。主路径 CPU。

### 3.2 SenseVoice：CPU vs DirectML

1. pip `sherpa-onnx` 自带 ORT ≈1.28，**无 DML**；`provider=directml` 时实测仍近 CPU（`sensevoice-cpu-ab` 整段中位 298.6ms）。
2. 向 sherpa 热替换 `onnxruntime-directml` 1.24.4 DLL：ORT API 版本不一致 → **崩溃**，不可用。
3. 同特征纯 ONNX forward（`scripts/bench_sensevoice_ort_forward.py`）：

| tag | frames | provider | forward 中位 |
|-----|--------|----------|--------------|
| `sensevoice-ort-cpu90` | 90 | CPU | **258.5** |
| `sensevoice-ort-dml90` | 90 | DML | 745.6 |
| `sensevoice-ort-cpu40` | 40 | CPU | **129.3** |
| `sensevoice-ort-dml40` | 40 | DML | 549.6 |

**结论：弃用 DirectML。** 同输入下 DML 约慢 3–4×。主路径 CPU。

### 3.3 Opus-MT LoRA「截断中文前缀 → 完整英文」

目的：让 MT 更适合双轨「只译已确认前缀」。数据约 200 条，LoRA r=8，2 epoch（CPU ~3.5min），合并后转 CT2：`models/opus-mt-zh-en-lora-ct2`。评测：`docs/prefix_mt_lora_ab.json`，tag=`prefix-mt-lora-ab`。

| 项 | 数值 |
|----|------|
| 用例数 | 36 |
| base CT2 延迟中位 | **48.7 ms** |
| LoRA CT2 延迟中位 | **61.6 ms**（更慢） |
| 质量 | 完整句偶有小改善；短前缀易幻觉（例：「今天的会」→ 臆补 New York） |

**结论：不接入主路径。** 配置仍指向原 `opus-mt-zh-en-ct2`。若重做需更大平行语料，且目标应是「忠实译已确认前缀」，而非补全未说完的整句。

### 3.4 明确不做（首版，见 ROADMAP）

- MT 上核显 / ROCm（CT2 无 DirectML；Renoir 不在 ROCm 范围）
- 翻译 LLM 进首句关键路径
- NAST / 全面 LLM SimulMT
- 照搬 DeepSeek 的 PD 分离、CUDA Graph、MoE 部署

---

## 阶段 4：MT 门控与结构加固

### 4.1 REINA-lite（接入）

**改动：** `app/mt_policy.py`，`MT_POLICY=reina`：内容信息分 + 句末虚词挂起；字数/句界仍作安全阀。完整论文级音频 READ/WRITE 策略网**未做**。

| tag | policy | first_stable_mt | wall | mt_triggers | mt_waits | 说明 |
|-----|--------|-----------------|------|-------------|----------|------|
| `dualsim-chars-ab` | chars | 509.0 | 1203.3 | 3 | 2 | 纯字数≥6 |
| `dualsim-reina` | reina（早期阈值，偏 4.0） | **316.8** | 1089.3 | 3 | 2 | 首译快，但易过早碎译（如过早 “Welcome.”） |
| **`dualsim-reina-5.5`** | **reina，`MT_INFO_MIN=5.5`** | 480.7 | **1059.2** | **2** | 3 | **采用**：相对 chars，中途 MT 3→2，wall 1203→1059 |

**结论：** 默认 `MT_POLICY=reina`，`MT_INFO_MIN=5.5`；可用 `chars` 回退。

### 4.2 DeepSeek 评审落地的结构项

落地内容：

- pending **1.5s** 超时强制 WRITE（`MT_PENDING_MAX_WAIT_SEC=1.5`）
- WRITE 游标 `translated_src`；句尾 finalize 只补未译尾
- ASR `seq`；TTS `utt_id/seq` + `invalidate`（句尾丢掉未播队列）

自适应 partial 试验（**未保留为默认**）：

| tag | 自适应 | intervals（记录） | first_partial | first_stable_mt | wall | partials |
|-----|--------|-------------------|---------------|-----------------|------|----------|
| `dualsim-cursors-adapt` | 开 | 0.4→0.7 | 65.3 | 536.8 | **1403.6** | 8 |
| `dualsim-cursors-v2` | 开 | 0.4→0.7 | 56.1 | 486.7 | 1226.9 | 8 |
| `dualsim-cursors-v3` | 开 | 0.55→0.75 | 67.8 | 444.4 | 1289.6 | 7 |
| **`dualsim-cursors-v4`** | **关** | 全 0.75 | 78.1 | 476.8 | **1042.9** | 6 |

**结论：**

- 游标 / 超时 WRITE / TTS invalidate：**保留**（正确性）。
- 自适应 partial：**关闭**（`PARTIAL_ADAPTIVE=False`）；墙钟与碎译在 0.4/0.55 设定下变差。
- v4 在 SenseVoice + reina 下 wall 回到约 **1043 ms**，与 reina-5.5 同量级。

---

## 阶段 5：真流式 ASR（Online Paraformer，当前默认）

### 5.1 动机

SenseVoice 路径的「增量」仍是 **OfflineRecognizer + 重叠窗启发式**，句尾再整段确认。目标改为 sherpa **`OnlineRecognizer.from_paraformer`**，按波形增量 `accept_waveform`，句尾 `input_finished` 刷新，去掉重叠窗拼接。

模型：`models/sherpa-paraformer-online`（encoder/decoder int8 + tokens）。下载脚本：`scripts/download_sherpa_paraformer_online.py`。

### 5.2 接入要点（实现约束，测过）

- `ASR_BACKEND=sherpa_paraformer_online`
- 句尾必须与 partial **同一 `buf` 时间轴** finalize；若用 VAD 裁短的 `seg.audio`，易丢尾字
- 句尾需 **尾部静音 pad**（实现约 0.8s）再 `input_finished`；sherpa 文档示例亦有 ~0.5s tail padding
- `sample_rate` 传 **int**（传 float 会在 `FeatureExtractorConfig` 处 TypeError）

### 5.3 A/B（同一 dualsim、同一 wav、reina、partial=0.75）

| tag | 后端 | first_partial | first_stable_mt | wall | asr_ms_sum | 终稿 src（记录） |
|-----|------|---------------|-----------------|------|------------|------------------|
| `dualsim-sensevoice-ab` | sherpa_sensevoice | 82.9 | **456.2** | 1002.7 | 780.5 | `…打摩院…`（错字） |
| `dualsim-paraformer-online` | paraformer_online | 53.4 | 528.9 | 684.6† | 447.5 | `…的语音`（**不完整**，finalize 有 bug） |
| **`dualsim-paraformer-online-v2`** | paraformer_online | **60.0** | 547.7 | **920.4** | **652.0** | `…达摩院推出的语音识别模型`（完整且正确） |

† v1 的 wall/终稿**不可当作优胜依据**：final `samples=0`、缺「识别模型」。修复 pad + buf 后以 **v2** 为准。

**相对 SenseVoice A/B（采用 v2）：**

| 项 | SenseVoice | Online Paraformer v2 | 变化 |
|----|------------|----------------------|------|
| first_partial | 82.9 | 60.0 | 更快 |
| first_stable_mt | 456.2 | 547.7 | 约慢 90ms（流式假说更碎，稳定前缀更晚） |
| utt_wall | 1002.7 | 920.4 | 更快 |
| asr_ms_sum | 780.5 | 652.0 | 更低 |
| 终稿专用名 | 打摩院 | **达摩院** | 质量更好 |

**决策：** 默认 `ASR_BACKEND=sherpa_paraformer_online`。可用 `--asr-backend sherpa_sensevoice` 或改配置回退。

---

## 总览：采用 vs 回退

| 主题 | 决策 | 依据 tag / 证据 |
|------|------|-----------------|
| MT CT2 int8 | **采用** | `paraformer-ct2` |
| sherpa SenseVoice ONNX | 曾为默认；现作回退选项 | `sherpa-sensevoice-ct2` 等 |
| Piper + 中英预热 | **采用** | `sherpa-ct2-tts-warmup` |
| 双轨 + 音频时间轴 partial | **采用** | `dualsim-v2` / `v3` |
| Silero VAD | **采用** | 配置默认；与 condmt 同轮接入 |
| 条件 MT / 异步 TTS | **采用** | `dualsim-silero-condmt` 起 |
| ASR 重叠窗增量（SenseVoice） | 曾采用；被真流式替代 | `dualsim-overlap-0.5` vs `asr-full` |
| partial=0.5s | **回退**，保持 0.75s | `dualsim-partial-0.5` |
| Piper / SenseVoice DirectML | **弃用** | `piper-*-ab`，`sensevoice-ort-*-ab` |
| Opus-MT 前缀 LoRA | **不接入** | `prefix-mt-lora-ab` |
| REINA-lite @5.5 | **采用** | `dualsim-reina-5.5` vs `chars-ab` |
| 自适应 partial | **关闭** | `cursors-adapt/v2/v3` vs `v4` |
| 游标 / 超时 WRITE / TTS invalidate | **采用** | `cursors-v4` 保留项 |
| Online Paraformer | **默认采用** | `dualsim-paraformer-online-v2` vs `sensevoice-ab` |

---

## 当前默认水位（截至 paraformer-online-v2）

配置要点（以 `app/config.py` 为准，本文不代替源码）：

- ASR：`sherpa_paraformer_online`
- MT：CT2 Opus，`MT_POLICY=reina`，`MT_INFO_MIN=5.5`
- VAD：Silero，静音 0.35s
- partial：固定 0.75s；`PARTIAL_ADAPTIVE=False`
- TTS：Piper CPU，异步队列，启动预热

**dualsim（示例句、不播 TTS）大约：**

| 节点 | 约值（v2） |
|------|------------|
| first_partial | ~60 ms |
| first_stable_mt | ~550 ms |
| utt_wall | ~920 ms |
| asr_ms_sum | ~650 ms |

**仍未达到的目标（ROADMAP 原文）：** 停说后 **200–300ms** 出可用首句。当前 wall 已含 VAD 0.35s 静音；全本地 CPU 上先按 **&lt;1s 可用** 理解更贴切。

**已知质量问题（延迟优化未覆盖）：** 专名/组织名 MT 仍不稳（记录中「达摩院」常被译成 Dharma / hospital / Moor 等）；属 Opus-MT 词汇与领域，不是 ASR 延迟问题。

---

## 复现命令

```powershell
cd D:\xuanjian\realtime-translate
.\.venv\Scripts\Activate.ps1

# 整段
python scripts/bench.py --tag <标签> --note "说明"

# 双轨仿真（可覆盖 ASR 后端）
python scripts/bench_dualsim.py --tag dualsim-<标签> --note "说明"
python scripts/bench_dualsim.py --asr-backend sherpa_sensevoice --tag dualsim-sensevoice-ab --note "对照"
python scripts/bench_dualsim.py --asr-backend sherpa_paraformer_online --tag dualsim-paraformer-online-v2 --note "流式"

# 历史
# docs/bench_history.jsonl
# docs/OPTIMIZATION_LOG.md
```

---

## 文档关系

| 文件 | 用途 |
|------|------|
| `OPTIMIZATION_STAGES.md`（本文） | 分阶段叙事复盘：改动、数字、采用/回退 |
| `OPTIMIZATION_ROADMAP.md` | 清单、优先级、明确不做 |
| `OPTIMIZATION_LOG.md` | 汇总表 + bench 自动追加行 |
| `bench_history.jsonl` | 机器可读原始记录 |
