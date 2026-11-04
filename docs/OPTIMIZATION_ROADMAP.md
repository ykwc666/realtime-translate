# 优化清单（别忘）

> 硬件：Ryzen 7 4800H + 32GB，无 NVIDIA，Radeon 核显。  
> 当前默认：`sherpa Online Paraformer` + `Opus-MT CT2 int8` + `Piper` + 双轨（REINA-lite）+ SenseVoice 可回退。  
> 分阶段复盘（改动 / 数字 / 采用与回退）：`docs/OPTIMIZATION_STAGES.md`。  
> 测速与历史：`docs/OPTIMIZATION_LOG.md`、`docs/bench_history.jsonl`。

## 已完成

- [x] TTS 改离线 Piper（去 edge-tts 联网）
- [x] Opus-MT → CTranslate2 int8
- [x] ASR → sherpa SenseVoice int8 ONNX
- [x] 双轨：partial → 稳定前缀 → 增量 MT
- [x] TTS 中英预热
- [x] 测速脚本与优化日志
- [x] 累积 ASR 的稳定前缀逻辑
- [x] partial 按音频时间轴触发

## 进行中 / 下一刀（按优先级）

1. [x] **Silero VAD ONNX** 替换能量 VAD（`VAD_BACKEND=silero`，失败回退 energy）
2. [x] **条件 MT**：待译缓冲 ≥`MIN_MT_CHARS`(6) 或句界才触发（减少碎译）
3. [x] **异步 TTS 队列**：`TTS_ASYNC=True`，`app/tts_queue.py`
4. [x] **ASR 少重算（轻量）**：partial 文本 unchanged 则跳过
5. [x] **确认 AVX2**：`torch.backends.cpu.get_cpu_capability()=AVX2`；CT2 提供 `int8`；ORT `CPUExecutionProvider` Release 构建（自动 ISA dispatch）
6. [x] **ASR 增量窗**：`ASR_INCREMENTAL` + `ASR_OVERLAP_SEC=0.5`；partial 只喂尾窗，句尾仍整段确认；Latin 短误识丢弃 + 子串拼接

## 可选实验（有空再做）

- [x] partial 间隔 0.5s A/B → **回退 0.75s**（first_mt 更快但碎译/MT 次数↑、wall↑）
- [x] Piper → DirectML A/B → **回退 CPU**（Renoir 核显更慢：zh_mid 371→781ms，en_mid 151→538ms；脚本 `scripts/bench_piper_providers.py`，环境 `.venv-dml`）
- [x] SenseVoice → DirectML A/B → **回退 CPU**  
  - pip `sherpa-onnx` 自带 ORT≈1.28 **无 DML**；`provider=directml` 实际仍走 CPU  
  - 塞入 `onnxruntime-directml` 1.24.4 DLL → ORT API 28 vs 24 **崩溃**  
  - 纯 ONNX forward（同特征）：T90 CPU **259ms** vs DML **746ms**；T40 **129** vs **550**（约 3–4× 更慢）  
  - 脚本：`bench_sensevoice_providers.py` / `bench_sensevoice_ort_forward.py`
- [x] LoRA 前缀训练 Opus-MT → **不接入主路径（实验保留）**  
  - 数据：40 句 seed → 200 条「截断 ZH→完整 EN」；CPU LoRA r=8 ×2 epoch ~3.5min  
  - 合并后转 CT2：`models/opus-mt-zh-en-lora-ct2`  
  - A/B：延迟 base **48.7** vs lora **61.6** ms；短前缀易幻觉（如补出 New York），完整句偶有小改善但整体不稳  
  - 脚本：`build_prefix_mt_data.py` / `train_opus_lora_prefix.py` / `merge_opus_lora_ct2.py` / `bench_prefix_mt.py`  
  - 若再做：需更大平行语料 + 保守目标（译已确认前缀，而非臆补整句）
- [x] REINA 式信息增益判断「是否值得译」→ **接入**（轻量版，非论文策略网）  
  - `MT_POLICY=reina`：内容信息分 + 句末挂起虚词 WAIT；`MT_INFO_MIN=5.5`  
  - dualsim：相对 chars，中途 MT 3→2、wall 1203→1059；首稳译相近；挂起「的」会推迟碎译  
  - 完整 REINA（音频侧 READ/WRITE 策略网）仍不做；可 `MT_POLICY=chars` 回退  
  - 代码：`app/mt_policy.py`
- [x] **结构加固（DeepSeek 评审落地）**  
  - 自适应 partial：**试过 0.40/0.55 → wall↑ 碎译↑，已关**；固定 **0.75s**  
  - REINA pending **1.5s 时间超时**强制 WRITE（保留）  
  - WRITE 游标 `translated_src`；final 去标点对齐只补未译尾（保留）  
  - ASR `seq`；TTS `utt_id/seq` + `invalidate`（保留）  
  - dualsim-cursors-v4：first_stable_mt~477ms，wall~**1043ms**（回到改前水平）  

- [x] **真流式 ASR：sherpa Online Paraformer** → **接入并默认**  
  - 模型：`models/sherpa-paraformer-online`（encoder/decoder int8）  
  - `ASR_BACKEND=sherpa_paraformer_online`：`OnlineRecognizer` 增量 `accept_waveform`；句尾 **buf 时间轴 + 静音 pad + input_finished**（勿用 VAD 裁短 seg，否则丢尾字）  
  - dualsim A/B（同 wav）：SenseVoice first_partial~83 / first_mt~456 / wall~**1003** / ASR sum~781；Paraformer ~60 / ~548 / wall~**920** / ASR sum~652；终稿「达摩院」正确（SenseVoice 易「打摩院」）  
  - 首稳译略慢 ~90ms（流式假说更碎），wall/累计 ASR/终稿质量胜出 → 默认 Online Paraformer；`sherpa_sensevoice` 可回退  
  - 脚本：`download_sherpa_paraformer_online.py`；`--asr-backend` 可 A/B
- [x] **代码瘦身（2026-11-02）** → dualsim-slim-v1  
  - 热路径 ASR 仅 Paraformer + SenseVoice；去掉 FunASR 后端与文件切段 FunasrVad（改 `segment_audio`/Silero）  
  - `AudioBuffer` 分块追加；流式 `feed_stream` 只喂新波形；真流式强制同步 partial  
  - `ONLINE_TAIL_PAD_SEC=0.5`（原 0.8）  
  - 相对 paraformer-online-v2：wall **920→834**，first_mt **548→508**，ASR sum **652→589**，终稿仍完整
- [x] **音频路径 profiling（非 Rust）** → `scripts/bench_audio_path.py` tag=`audio-path-gilpure`  
  - 空载：100ms/40ms 回调间隔 p99≈标称，入队→消费 lag p99 **&lt;0.5ms**，无 drop/status  
  - 纯 Python 占 GIL：100ms lag p99~**32ms**；40ms lag p99~**93ms**、间隔 max~79ms → GIL 争用**确实**能打穿短块截止  
  - 但 ORT/CT2 计算期会放 GIL；当前默认 100ms 块 + 半双工下，空载路径不是 wall 主因  
  - Silero `feed`：100ms 块中位 ~1ms；20ms 在本机 PortAudio 易成对回调（中位间隔异常），不建议盲降  
  - **结论：Rust sidecar 有架构价值（极端 GIL/更短块/全双工），不作为当前降 wall 的下一步**

## 明确不做（首版）

- [ ] MT 上核显 / ROCm（CT2 无 DirectML；Renoir 也不在 ROCm 范围）
- [ ] 翻译 LLM（llama.cpp/Vulkan）进首句关键路径（质量档可另开）
- [ ] NAST / 全面转向 LLM SimulMT
- [ ] 照搬 DeepSeek 的 PD 分离、CUDA Graph、MoE 部署

## DeepSeek 可借鉴原则（落地检查）

- [x] 草稿-验证（partial + final）
- [x] 状态复用（ASR 增量重叠窗；MT 按缓冲合并；句尾整段确认）
- [x] 条件计算（MIN_MT_CHARS / 句界）
- [x] 乐观调度（TTS 异步队列）
- [x] 缩小同步粒度（TTS 队列事件；partial 后台线程）

## 目标体感

- 说话中已有草稿/部分译文
- 停止说话后首句：**尽量逼近 200–300ms**（全本地 CPU 现实目标先稳住 **<1s 可用**）

## 改完记得

```powershell
python scripts/bench.py --tag <标签> --note "说明"
python scripts/bench_dualsim.py --tag dualsim-<标签> --note "说明"
```

并把结果写入 `OPTIMIZATION_LOG.md`。
