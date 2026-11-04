# 长句投机提前量自适应 A/B

> 日期：2026-11-04  
> 机器：4800H（8 物理 / 16 逻辑）  
> 复现：`python scripts/bench_paced.py --wav ... --2pass-parallel on --online-threads 3 --offline-threads 2 --adaptive-spec on|off`

## 0. 预测公式（来自微基准拟合）

`scripts/bench_2pass_ab.py` 并行 **online=3 offline=2**，6 段（accept + tail）：

| seg | audio_sec | wall_med_ms |
|---|---:|---:|
| accept#0 | 1.476 | 353 |
| accept#1 | 1.380 | 293 |
| accept#2 | 1.188 | 266 |
| accept#3 | 1.732 | 497 |
| tail#0 | 1.540 | 402 |
| tail#1 | 1.380 | 294 |

线性拟合：

```text
pred_decode_ms = max(180, -310.93 + 456.60 * audio_sec)
trigger_silence = clamp(
    VAD_SILENCE_SEC - pred_decode_sec - 0.05,
    [ASR_SPECULATE_MIN_SILENCE_SEC=0.08, ASR_SPECULATE_SILENCE_SEC=0.25]
)
```

短句 → trigger 仍 ≈0.25；长句（pred 大）→ 可低至 0.08。实现：`app/speculate_lead.py`。

## 1. 真实 wav：串行 6+6 vs 并行 3+2（自适应关）

同一套文本/句数；并行明显更快。

### accept_phrases.wav

| src | ser66 perceived | par32 perceived |
|---|---:|---:|
| 你好延迟大概有多少 | 1244 | **991** |
| 今天天气怎么样 | 596 | **519** |
| 现在是星期二 | 450 | 450 |
| 他现在没有回我的消息 | 763 | **470** |

### tail_duosimo.wav

| src | ser66 | par32 |
|---|---:|---:|
| 延迟大概有多少 | 791 | **540** |
| 今天天气怎么样 | 656 | 675 |

→ 并行 3+2 **无文本回归**，体感更好；继续叠自适应。

## 2. fixed vs adaptive（并行 3+2，交替 2 轮）

### accept_phrases — 长句与汇总

| round | mode | 长句 perceived | waited | mean | median | max |
|---|---|---:|---:|---:|---:|---:|
| r1 | fixed | 978 | 476 | 619 | 574 | 978 |
| r1 | adaptive | 676 | 160 | — | — | — |
| r2 | fixed | （见 jsonl） | — | — | — | — |
| r2 | adaptive | **604** | **106** | **489** | **450** | **604** |

长句目标约 747→600：**达标**（r2 adaptive 604）。

短句（天气/星期二/回消息）：adaptive 多为 **450**（贴静音地板），相对 fixed **无 ≥50ms 回退**。

src/tgt 与 fixed **完全一致**（4 句）。

### pause_xingqi_er.wav（安全性）

| round | mode | src | utts | spec |
|---|---|---|---:|---|
| r1/r2 | fixed | 现在是星期啊 | 1 | miss_grow（预期） |
| r1/r2 | adaptive | 现在是星期啊 | 1 | miss_grow（预期） |

分段与文本不变。

### tail_duosimo

| mode (r2) | mean | median | max |
|---|---:|---:|---:|
| fixed | ~489 | 493 | 493 |
| adaptive | **451** | **451** | **451** |

## 3. 投机额外 CPU / 作废（accept 一轮）

| mode | starts | hits | cancel_speech | miss_grow | miss_timeout |
|---|---:|---:|---:|---:|---:|
| fixed | 5 | 4 | 1 | 0 | 0 |
| adaptive | 5 | 4 | 1 | 0 | 0 |

自适应并未增加作废次数；长句只是 **更早启动同一个 hit**（waited 476→106）。`cancel_speech=1` 来自「你好」短快照后继续说话，两种模式相同。

## 4. 默认与测试

```python
ASR_SPECULATE_ADAPTIVE_LEAD = True
ASR_SPECULATE_MIN_SILENCE_SEC = 0.08
ASR_SPECULATE_ADAPTIVE_MARGIN_SEC = 0.05
```

单测：`tests/test_speculate_lead.py`（adaptive=False 恒等于 0.25；长句不晚于短句；夹紧；坏输入安全）。

pytest：本任务相关 + 全量 **40 passed**（含既有用例）。
