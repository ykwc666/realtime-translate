# 安全提前收句？固定阈值 A/B + 只读 early-cut probe

> 日期：2026-11-04  
> 原始：`docs/endpoint_silence_ab.jsonl`、`docs/endpoint_early_cut_probe.jsonl`  
> 复现：`.\.venv\Scripts\python.exe scripts\bench_endpoint_early_cut_ab.py --phase all --reps 2`  
> 测试：`64 passed`（含 `tests/test_early_cut_probe.py`）  
> **默认未改**：`VAD_SILENCE_SEC=0.45`，`ENDPOINT_EARLY_CUT_PROBE=False`

## 结论（先看这）

1. **固定降到 0.35 / 0.40（本批合成 wav）**：无 merge / split / tail_loss；气口三件套 src 与 0.45 一致；`speculate_hit` 不降。device TTFA 中位约 **−80~−150ms**。  
2. **仍不升默认**：合成夹具过关 ≠ 实麦气口过关；默认保持 **0.45**。  
3. **「投机已就绪则提前收句」这条生产线关闭**：在仍按 0.45 收句的只读探针里，**safe_350 = 0%、safe_400 = 0%**（检查点到达时 `spec_done` 几乎总是 false）。固定 0.35 能变快，靠的是 **CUT 后仍可 LATE_WAIT 等投机**，不是「0.35 时文本已经等于终稿」。  
4. **不写生产版 early-cut**；下一步若还动静音，只做人耳气口验收后再谈是否把默认改为 0.35。

## Phase A：固定阈值（Silero，0.45 为参考）

硬判据：任一 wav 出现 merge/split/tail_loss，或气口 wav 与 0.45 src 不一致 → 不安全。

| silence | 记录数 | unsafe |
|---|---:|---:|
| 0.45 | 20 | **0** |
| 0.40 | 20 | **0** |
| 0.35 | 20 | **0** |

### 端到端子集（`--tts on --persistent on`）device TTFA / mt_ready 中位（两 rep）

| wav | 0.45 mt / device | 0.40 mt / device | 0.35 mt / device |
|---|---|---|---|
| accept_phrases | 450–505 / **609–637** | 400 / **558–571** | 350–402 / **482–511** |
| tail_duosimo | 450 / **570–622** | 400 / **504–538** | 350–428 / **496–568** |

accept 上 0.45→0.35 device 中位大约 **−100ms+**（满足「≥80ms」量级）。  
`t_cut_ms` 贴近 450/400/350。短句 `asr_wait_after_cut` 多在 0–80ms；`pause_xingqi` / 部分气口句 hit=0，CUT 后仍要等 ~250–290ms（与阈值无关的 ASR 尾巴）。

气口 wav（tts off/on）示例 src（与 0.45 一致）：
- `pause_xingqi_er` → `现在是星期啊`
- `stop_zheshi_xingqi` → `这是星期`
- `cont_tuesday` → `现在是星期二`

## Phase B：只读 probe（地板仍 0.45）

每句在静音跨过 0.30/0.35/0.40/0.45 时各拍一帧；**不触发/取消投机，不改 arbiter**。

| 分组 | n | ready@350 | same@350 | resumed | **safe_350** | safe_400 |
|---|---:|---:|---:|---:|---:|---:|
| long | 8 | 0% | 0% | 25% | **0%** | **0%** |
| breath | 3 | 0% | 0% | 0% | **0%** | **0%** |

`earliest_safe` 全为 None。  
含义：在 0.45 地板下，静音刚到 0.35/0.40 时二遍投机通常**还没 done**；不能把「最终 0.45 的 src」当成当时可提前切的证据。

音频线程：probe off median **2.1ms** / on **2.2ms**；`over_100ms` 同为 4/139。max 升高（164→274）可接受，无级联超支。

## 门槛对照（提示词 §4）

| # | 条件 | 结果 |
|---|---|---|
| 1 | 固定 0.35 气口集不碎/不粘/不丢尾 | **过**（合成） |
| 2 | oracle 气口 false-safe=0 且短句 safe_350≥80% | **不过**（safe_350=0%） |
| 3 | device TTFA 中位改善 ≥80ms | **过**（accept） |
| 4 | spec hit 不降 / 等待不吃光收益 | **过**（命中句） |
| 5 | probe 不明显拖垮音频线程 | **过**（median） |

因 oracle **无法**支撑「就绪即切」，**不进入生产 early-cut 开发**（线关闭）。  
固定 0.35 仅说明「缩短静音地板」在夹具上可行，**不等于**实现投机门控提前切。

## 人耳仍需确认

1. 气口句是否被切碎（实麦「现在是星期…二」）  
2. 尾字是否完整  
3. 连续两句是否合并  

## 开关

```python
VAD_SILENCE_SEC = 0.45                 # 保持
ENDPOINT_EARLY_CUT_PROBE = False       # 只读探针默认关；bench --probe on
```
