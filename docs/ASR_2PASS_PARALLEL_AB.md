# 二遍 ASR 并行：抢核诊断与体感验收

> 日期：2026-11-04  
> 机器：Ryzen 7 4800H（**8 物理核 / 16 逻辑核**）  
> 入口：`app/asr_stream.py` `_run_2pass_pair` / `transcribe_with_2pass` / `finalize_stream`  
> 复现：`python scripts/bench_2pass_ab.py --threads 6 4 3 2 --reps 3 --parallel both`  
> 体感：`python scripts/bench_paced.py --spec on --silence 0.45 --tag ...`

## 1. 为何 6+6 并行没有收益

收句关键路径同时跑：

1. 流式 Paraformer oneshot / finalize  
2. 离线 SenseVoice  

旧配置两路都用 `ASR_THREADS=6` → 并行时 **12 个 ORT 线程抢 8 物理核**，单路耗时涨约 1.6×，墙钟几乎不降（甚至略差）。

| 配置 | wall_med（6 段中位） | 说明 |
|---|---:|---|
| 串行 6+6 | 472 ms | 基线 |
| 并行 6+6 | 455 ms | 几乎无赚；`model_med` 涨到 695（两路之和记账） |

文本 6 段与串行完全一致。

## 2. 改动

- `ASR_THREADS_ONLINE` / `ASR_THREADS_OFFLINE` 拆开（`ASR_THREADS=6` 仍作兜底）
- Paraformer ← ONLINE；SenseVoice ← OFFLINE
- 微基准：`scripts/bench_2pass_ab.py`

## 3. 微基准摘要（`accept_phrases` + `tail_duosimo`，reps=3）

| online | offline | parallel | wall_med | wall_min | model_med |
|---:|---:|---|---:|---:|---:|
| 6 | 6 | off | 472 | 390 | 472 |
| 4 | 4 | off | 423 | 385 | 423 |
| 3 | 3 | off | 424 | 371 | 424 |
| 2 | 2 | off | 464 | 380 | 464 |
| 6 | 6 | on | 455 | 389 | 695 |
| 4 | 4 | on | 346 | 289 | 514 |
| 3 | 3 | on | 333 | 307 | 494 |
| **3** | **2** | **on** | **323** | **266** | 508 |
| 2 | 2 | on | 364 | 279 | 516 |

并行 3+2 相对最佳串行 4+4：墙钟中位约 **−100 ms**。文本全部一致。

## 4. 体感口径（真正判据）

| tag | 配置 | mean | median | max |
|---|---|---:|---:|---:|
| `paced-2pass-ser6` | 串行 6/6 | 704 | **622** | 1040 |
| `paced-2pass-par3-2` | 并行 3/2 | 552 | **458** | 747 |

median **−164 ms**，max **−293 ms**。

## 5. 默认值（已落地）

```python
ASR_THREADS = 6                 # 兜底
ASR_THREADS_ONLINE = 3          # 流式 Paraformer
ASR_THREADS_OFFLINE = 2         # SenseVoice 二遍
ASR_2PASS_PARALLEL = True
```

依据：上表 wall_med 最优并行组合 + paced perceived median 622→458。

**结论：** 并行不是该关，而是 **不能两路都开 6 线程**；压到约 3+2 才有净收益。

## 6. 说话中 partial（`feed_stream`）

调小 `ASR_THREADS_ONLINE` **几乎不影响** 说话期间每块耗时（100ms 块）：

| online_threads | median_ms | p90 | max |
|---:|---:|---:|---:|
| 6 | 0.46 | 0.82 | 73 |
| 4 | 0.33 | 0.61 | 63 |
| 3 | 0.38 | 0.62 | 61 |
| 2 | 0.30 | 0.54 | 79 |

中位均 &lt;1ms，远低于块预算。
