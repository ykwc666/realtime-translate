# 优化记录

机器：AMD Ryzen 7 4800H，32GB RAM，无 NVIDIA，Windows。  
测速音频：FunASR 自带 `asr_example.wav`（约 5.55s 中文）。  
分阶段叙事复盘（含采用/回退依据）：[`OPTIMIZATION_STAGES.md`](./OPTIMIZATION_STAGES.md)。  
复现：

```powershell
cd D:\xuanjian\realtime-translate
.\.venv\Scripts\Activate.ps1
python scripts/bench.py --tag <标签> --note "说明"
python scripts/bench_dualsim.py --tag dualsim-xxx --note "双轨仿真"
```

机器可读历史：`docs/bench_history.jsonl`

## 汇总（中位 ms）

| 时间 | 标签 | ASR | MT | TTS | E2E | 改动 |
|------|------|-----|----|-----|-----|------|
| 2026-11-02 | baseline-paraformer-transformers | 2430 | 837 | 0 | 3333 | Paraformer + transformers Opus |
| 2026-11-02 | paraformer-ct2 | 2027 | 80 | 0 | 2105 | MT → CT2 int8 |
| 2026-11-02 | sensevoice-ct2 | 758 | 98 | 242 | 1097 | SenseVoice PyTorch |
| 2026-11-02 | sherpa-sensevoice-ct2 | 331 | 103 | 838* | 1272* | sherpa ONNX（TTS 未预热） |
| 2026-11-02 | **sherpa-ct2-tts-warmup** | 459 | 206 | **401** | 1262 | **TTS 中英预热**；首轮 TTS 255ms |

\* 未预热时 TTS 冷启动可到 ~840ms。

### 双轨仿真（dualsim）

| 时间 | 标签 | partials | first_partial | first_stable_mt | 句尾 wall | 说明 |
|------|------|----------|---------------|-----------------|-----------|------|
| 2026-11-02 | dualsim-v1 | 0 | — | — | 360ms | bug：墙钟计时 |
| 2026-11-02 | dualsim-v2 | 5 | 89ms | — | 1116ms | 音频时间轴；稳定前缀未适配累积 ASR |
| 2026-11-02 | dualsim-v3 | 5 | 88ms | 239ms | 1274ms | 累积 ASR 稳定前缀生效 |
| 2026-11-02 | dualsim-silero-condmt | 6 | 85ms | 623ms | 1560ms | Silero + 条件 MT + 异步 TTS |
| 2026-11-02 | dualsim-asr-full（对照） | 6 | 76ms | 595ms | 1533ms | 增量关：partial 样本递增到 76k；ASR 累计 1261ms |
| 2026-11-02 | dualsim-asr-incr3 | 6 | 86ms | 504ms | 1171ms | 增量开：partial 封顶 25.6k；ASR 累计 870ms |
| 2026-11-02 | dualsim-partial-0.5 | 10 | 56ms | 296ms | 1521ms | **回退**：间隔过密→碎译/MT×6、wall↑ |
| 2026-11-02 | dualsim-asr-incr4 | 6 | 76ms | 505ms | 1161ms | 增量 overlap=0.8；ASR 累计 880ms |
| 2026-11-02 | **dualsim-overlap-0.5** | 6 | **87ms** | **478ms** | **1144ms** | **当前**：overlap=0.5；ASR 累计 **831ms**（对照 full -34%） |

### CPU / ISA

- `torch.backends.cpu.get_cpu_capability()` → **AVX2**（4800H）
- CTranslate2 CPU compute types：`float32 / int8 / int8_float32`（当前 MT=int8）
- ONNX Runtime 1.31.1：`CPUExecutionProvider`（Release；运行时按 CPU 选 AVX2 内核）
- 线程：PyTorch/OpenMP 可见 8 worker（16 逻辑核）

### Piper CPU vs DirectML（独立 `.venv-dml`，中位 ms）

| provider | zh_short | zh_mid | en_short | en_mid | 结论 |
|----------|----------|--------|----------|--------|------|
| CPU（主环境） | 53 | 371 | 56 | 151 | **采用** |
| DirectML | 293 | 781 | 272 | 538 | **弃用**（约 2–5× 更慢） |

复现：`.\.venv\Scripts\python.exe scripts\bench_piper_providers.py --provider cpu`  
`.\.venv-dml\Scripts\python.exe scripts\bench_piper_providers.py --provider dml`

### SenseVoice CPU vs DirectML

| 测法 | CPU | DirectML | 结论 |
|------|-----|----------|------|
| sherpa 整段 wav（`provider=`） | 299ms | 340ms* | *无 DML DLL，等同 CPU |
| ONNX forward T=90 | **259ms** | 746ms | **弃用** |
| ONNX forward T=40 | **129ms** | 550ms | **弃用** |

Renoir 核显 + SenseVoice int8：DML 明显更慢；且与 pip sherpa 的 ORT 1.28 无法热替换。主路径保持 CPU。

### Opus-MT LoRA 前缀（截断 ZH → 完整 EN）

| 项 | 结果 |
|----|------|
| 训练 | 200 条，r=8，2 epoch，CPU ~3.5min，loss 2.34→2.03 |
| 推理 | 合并 + CT2 int8（未改主配置，仍用原 `opus-mt-zh-en-ct2`） |
| 延迟中位 | base 48.7ms / lora 61.6ms |
| 质量 | 完整句偶好；短前缀幻觉增多 → **不接入** |

评测明细：`docs/prefix_mt_lora_ab.json`

### 实机参数（当前）

> 以 `app/config.py` 为准；完整阶段说明见 `OPTIMIZATION_STAGES.md`。

- ASR 默认：**sherpa Online Paraformer**（`sherpa_paraformer_online`）；`sherpa_sensevoice` 可回退  
- VAD：**Silero ONNX**（失败回退能量 VAD），静音 0.35s  
- partial 间隔 0.75s（音频时间轴）；`PARTIAL_ADAPTIVE=False`；unchanged 跳过  
- SenseVoice 路径仍支持重叠窗增量：`ASR_INCREMENTAL` + `ASR_OVERLAP_SEC=0.50`；Online Paraformer 为真流式，句尾 buf + 静音 pad + `input_finished`  
- MT 门控：**reina**（信息分≥5.5 + 挂起虚词等待；`MT_POLICY=chars` 可回退）  
- 条件 MT 底线：字数/句界仍作安全阀  

### REINA-lite vs chars（dualsim）

| policy | first_stable_mt | wall | mt_triggers | waits | 说明 |
|--------|-----------------|------|-------------|-------|------|
| chars | 509ms | 1203ms | 3 | 2 | 纯字数≥6 |
| reina@4.0 | 317ms | 1089ms | 3 | 2 | 过早「Welcome.」碎译 |
| reina@5.5 | 481ms | 1059ms | 2 | 3 | 信息门控基线 |
| **cursors-v2** | **487ms** | **1227ms** | 3 | 3 | 自适应0.4→0.7 + 游标/超时/TTS失效；first_partial~56ms |

- 异步 TTS 队列；中途播报 ≥10 字或遇句号  
- 启动预热：ASR/MT + Piper 中英  
- 待办清单：`docs/OPTIMIZATION_ROADMAP.md`

### 相对基线

- ASR：2430 → ~330–460 ms  
- MT：837 → ~100–200 ms  
- TTS 首句：冷启动 ~840 → 预热后首轮 ~255  

## 自动追加

（由 bench / bench_dualsim 维护）
| 2026-11-02 19:33:07 | dualsim-v3 | first_partial=88.5 first_stable_mt=239.4 wall=1273.9 partials=5 | 累积ASR稳定前缀+音频时间轴 |
| 2026-11-02 19:42:19 | dualsim-silero-condmt | first_partial=84.8 first_stable_mt=622.9 wall=1559.9 partials=6 | Silero VAD + 条件MT + 异步TTS队列 |
| 2026-11-02 19:54:43 | dualsim-partial-0.5 | first_partial=56.1 first_stable_mt=295.9 wall=1521.1 partials=10 | 间隔0.5s：碎译增多，回退0.75 |
| 2026-11-02 19:55:35 | dualsim-asr-incr4 | first_partial=76.3 first_stable_mt=505.1 wall=1160.6 partials=6 | 增量0.75s+Latin剥离 |
| 2026-11-02 19:57:16 | dualsim-overlap-0.5 | first_partial=86.8 first_stable_mt=477.5 wall=1143.9 partials=6 | 重叠窗0.5s（当前） |
| 2026-11-02 19:49:54 | dualsim-asr-incr | first_partial=78.7 first_stable_mt=362.7 wall=1218.3 partials=6 | ASR增量重叠窗+文本拼接 |
| 2026-11-02 19:52:05 | dualsim-asr-incr2 | first_partial=80.6 first_stable_mt=1456.0 wall=1777.5 partials=6 | ASR增量+误识前缀丢弃/无对齐回退整段 |
| 2026-11-02 19:53:30 | dualsim-asr-incr3 | first_partial=85.7 first_stable_mt=503.6 wall=1170.9 partials=6 | ASR增量重叠窗+Latin误识丢弃+子串拼接 |
| 2026-11-02 19:54:43 | dualsim-partial-0.5 | first_partial=56.1 first_stable_mt=295.9 wall=1521.1 partials=10 | partial间隔0.5s+ASR增量 |
| 2026-11-02 19:55:35 | dualsim-asr-incr4 | first_partial=76.3 first_stable_mt=505.1 wall=1160.6 partials=6 | 增量0.75s+强化Latin前缀剥离 |
| 2026-11-02 19:57:16 | dualsim-overlap-0.5 | first_partial=86.8 first_stable_mt=477.5 wall=1143.9 partials=6 | ASR增量重叠窗0.5s（采用） |
| 2026-11-02 20:01:14 | piper-cpu-ab | zh_short=52.8ms zh_mid=371.2ms en_short=55.9ms en_mid=151.1ms | provider=cpu ort=1.31.1 |
| 2026-11-02 20:01:43 | piper-dml-ab | zh_short=292.7ms zh_mid=780.7ms en_short=272.2ms en_mid=538.4ms | provider=dml ort=1.24.4 |
| 2026-11-02 20:04:40 | sensevoice-cpu-ab | asr_median=298.6 min=293.0 | provider=cpu text=欢迎大家来体验达摩院推出的语音识别模型。 |
| 2026-11-02 20:06:46 | sensevoice-ort-cpu | forward_median=266.7 frames=90 | ort=1.31.1 sess=['CPUExecutionProvider'] |
| 2026-11-02 20:07:40 | sensevoice-ort-cpu90 | forward_median=258.5 frames=90 | ort=1.31.1 sess=['CPUExecutionProvider'] |
| 2026-11-02 20:07:52 | sensevoice-ort-dml90 | forward_median=745.6 frames=90 | ort=1.24.4 sess=['DmlExecutionProvider', 'CPUExecutionProvider'] |
| 2026-11-02 20:07:57 | sensevoice-ort-cpu40 | forward_median=129.3 frames=40 | ort=1.31.1 sess=['CPUExecutionProvider'] |
| 2026-11-02 20:08:09 | sensevoice-ort-dml40 | forward_median=549.6 frames=40 | ort=1.24.4 sess=['DmlExecutionProvider', 'CPUExecutionProvider'] |
| 2026-11-02 20:17:52 | prefix-mt-lora-ab | base_ms=48.7 lora_ms=61.6 n=36 | prefix LoRA A/B |
| 2026-11-02 20:31:12 | dualsim-reina | first_partial=77.7 first_stable_mt=316.8 wall=1089.3 partials=6 | MT_POLICY=reina 信息增益门控 |
| 2026-11-02 20:32:37 | dualsim-reina-5.5 | first_partial=77.7 first_stable_mt=480.7 wall=1059.2 partials=6 | reina MT_INFO_MIN=5.5 |
| 2026-11-02 21:15:00 | dualsim-cursors-adapt | first_partial=65.3 first_stable_mt=536.8 wall=1403.6 partials=8 | 自适应partial+超时WRITE+三游标/TTS失效 |
| 2026-11-02 21:16:32 | dualsim-cursors-v2 | first_partial=56.1 first_stable_mt=486.7 wall=1226.9 partials=8 | 游标标点对齐+自适应partial |
| 2026-11-02 21:18:54 | dualsim-cursors-v3 | first_partial=67.8 first_stable_mt=444.4 wall=1289.6 partials=7 | 自适应0.55→0.75 收回wall回归 |
| 2026-11-02 21:19:40 | dualsim-cursors-v4 | first_partial=78.1 first_stable_mt=476.8 wall=1042.9 partials=6 | 关自适应partial=0.75 保留游标/超时/TTS失效 |
| 2026-11-02 21:36:53 | dualsim-sensevoice-ab | first_partial=82.9 first_stable_mt=456.2 wall=1002.7 partials=6 | A/B baseline SenseVoice overlap |
| 2026-11-02 21:37:33 | dualsim-paraformer-online | first_partial=53.4 first_stable_mt=528.9 wall=684.6 partials=6 | A/B true streaming Online Paraformer |
| 2026-11-02 21:39:19 | dualsim-paraformer-online-v2 | first_partial=60.0 first_stable_mt=547.7 wall=920.4 partials=6 | Online Paraformer + tail pad + buf finalize |
| 2026-11-02 21:49:02 | dualsim-slim-v1 | first_partial=55.5 first_stable_mt=507.6 wall=834.4 partials=6 | 瘦ASR+AudioBuffer+sync流式+pad0.5 |
| 2026-11-02 21:49:46 | dualsim-slim-sensevoice | first_partial=81.5 first_stable_mt=465.4 wall=1236.6 partials=6 | 回退路径回归 SenseVoice |
| 2026-11-02 21:53:16 | audio-path-gil | block=100 gil=False int_p99=100.14280000177678 lag_p99=0.11719999747583643 late=0.0% | audio path profile |
| 2026-11-02 21:53:23 | audio-path-gil | block=40 gil=False int_p99=40.14929999539163 lag_p99=0.2375999974901788 late=0.0% | audio path profile |
| 2026-11-02 21:53:29 | audio-path-gil | block=20 gil=False int_p99=40.09850000147708 lag_p99=0.2552999940235168 late=49.83% | audio path profile |
| 2026-11-02 21:53:35 | audio-path-gil | block=100 gil=True int_p99=100.15610000118613 lag_p99=0.1407999952789396 late=0.0% | audio path profile |
| 2026-11-02 21:53:41 | audio-path-gil | block=40 gil=True int_p99=40.273200000228826 lag_p99=0.2913999996962957 late=0.0% | audio path profile |
| 2026-11-02 21:53:47 | audio-path-gil | block=20 gil=True int_p99=40.154700000130106 lag_p99=0.2796000044327229 late=49.83% | audio path profile |
| 2026-11-02 21:54:13 | audio-path-gilpure | block=100 gil=False int_p99=100.1096999971196 lag_p99=0.44949999573873356 late=0.0% | audio path profile |
| 2026-11-02 21:54:19 | audio-path-gilpure | block=40 gil=False int_p99=40.135700000973884 lag_p99=0.1646999953663908 late=0.0% | audio path profile |
| 2026-11-02 21:54:24 | audio-path-gilpure | block=100 gil=True int_p99=111.56340000161435 lag_p99=32.12110000458779 late=0.0% | audio path profile |
| 2026-11-02 21:54:29 | audio-path-gilpure | block=40 gil=True int_p99=63.73470000107773 lag_p99=92.58790000603767 late=2.42% | audio path profile |
| 2026-11-03 13:54:33 | dualsim-asr-tail-v1 | first_partial=73.7 first_stable_mt=1047.1 wall=1047.8 partials=7 | closing-chunk buf + pad0.8 + prefer_final_asr |
