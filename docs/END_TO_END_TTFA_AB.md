# 端到端真实首音 + 持久流稳定性

> 日期：2026-11-04  
> 原始记录：`docs/end_to_end_ttfa_ab.jsonl`（paced 明细另见 `docs/paced_bench.jsonl`）  
> 复现压力：`.\.venv\Scripts\python.exe -m pytest tests/test_tts_persist_stress.py -q`  
> 复现 paced：`.\.venv\Scripts\python.exe scripts\bench_paced.py --wav cache/ab_weekday/accept_phrases.wav --tts on --persistent on`

## 结论

1. **持久流打断→续播**：20×（长句打断→短句→短句）在 persistent on/off 上 **0 失败**；`stop` 不靠全局 `sd.stop()`（persistent）。  
2. **空闲补零 ≠ active underrun**：流空闲 ≥300ms 后再播，预载单 buffer 句 `active_underruns=0`。  
3. **真实设备首音已接通**：`latency_device_ttfa_ms` = 停说 → callback 首次写出本句非零 PCM；`latency_perceived_ms` 仍为 **mt_ready**（历史口径不变）。  
4. **late event**：`FINALIZE_WAIT_TTS=False` 下按 `utt_id` 有界回填；下一句 `invalidate→stop` 打断上一句时，**仍保留已发生的 first_callback**（修过一处「interrupted 就丢弃首音」的逻辑）。  
5. 全量测试：**58 passed**。默认仍 `TTS_PERSISTENT_OUTPUT=True`。

## Phase1 改动（正确性）

| 风险 | 结果 | 修复 |
|---|---|---|
| stop 往队列塞裸 `None` 哨兵 → 下一句早停 | 压力测试未再复现早停；仍加固 | PCM/sentinel 均带 **generation**；`stop` 只 bump gen + drain，**不再 put None** |
| 空闲补零计 underrun | 已区分 | `active_underruns` 仅在 `_persist_need_pcm`；另有 `idle_callbacks` |
| `sd.play` 回退冒充首音 | 已挡 | `callback_valid=False`，`first_callback_t=None` |
| 全静音伪造首音 | 已挡 | 无非零写出则不设 `first_callback_t` |

## Phase2 事件链路（为何不写错句）

```
callback 只记 perf_counter 绝对时间
  → PlayTiming.first_callback_t
  → TtsQueue 工作线程组 TtsPlayEvent(utt_id, seq, version, …)
  → DualTrackEngine._utt_stats[utt_id] 回填
```

- finalize 在 **enqueue 前** `_register_utt_stats(self.stats)`，并保存 `_utt_speech_end[utt_id]`。  
- `reset_utt()` 换新 `self.stats`，旧对象仍在 map（上限 16）。  
- paced `--tts on` 结束前 `wait_tts_idle()`，保证 late event 已合并。

## 字段口径

| 字段 | 含义 |
|---|---|
| `latency_perceived_ms` / 展示 mt_ready | speech_end → 译文入队（**旧口径，勿改义**） |
| `t_first_audio_ms` | 同上，**deprecated** |
| `t_mt_enqueue_ms` | 同上，显式名 |
| `t_tts_pcm_ms` | speech_end → 首段 PCM 就绪 |
| `latency_device_ttfa_ms` / `t_first_audio_callback_ms` | speech_end → **设备首音**（本轮主指标） |
| `mt_ready_to_callback_ms` | MT ready → 设备首音 |

## Paced A/B（真实扬声器）

文本与句数：`--tts on` 与 `--tts off` 一致（accept 4 句 / tail 2 句；src/tgt 相同）。

### accept_phrases.wav

| 模式 | mt_ready median | device_ttfa median | mean / p90 / max | underrun |
|---|---:|---:|---|---|
| tts off | 450 | — | — | — |
| tts on, persistent **off** | 450 | **653** | 674 / 653 / 863 | 0 |
| tts on, persistent **on** | 450 | **619** | 587 / 619 / 639 | 0 |
| off 复测（log） | — | **681** | 654 / 681 / 721 | 0 |

off−on 中位差约 **35–60ms**（drain/open 收益在端到端里被 VAD≈450ms 稀释）。

### tail_duosimo.wav（两轮交错）

| 轮 | persistent off median | persistent on median | Δ |
|---|---:|---:|---:|
| 1 | 625 | 560 | +65 |
| 2 | 642 | 596 | +46 |

音频线程：`--tts on` 时 accept `over_100ms` 约 2–5/139；tail 约 0–1/54。mt_ready 相对 tts off **无退化**。

## 延迟账本（短句、speculate hit 典型）

| 段 | 大约 | 说明 |
|---|---:|---|
| VAD cut（静音） | **~450ms** | 当前地板，本轮未动 |
| CUT→ASR done | **~0–50ms** | 投机命中时已算完 |
| ASR→MT ready | **~0–60ms** | TM 命中可 ≈0 |
| MT ready→PCM | **~70–130ms** | 合成 |
| PCM→设备 callback | **~10–80ms** | persistent 更小 |
| **device TTFA 合计** | **~540–680ms** | 主指标 |

剩余最大头仍是 **VAD 0.45s 静音门槛**，不是 TTS 开流。

## 人工听感（勿用样本数代替）

1. 连续两句英文  
2. Tuesday 尾音节  
3. 打断后下一句  
4. 多子句长句首音  

## 下一步只测一个方向

**在不动默认的前提下，用 paced `--tts on` 尺子，只对「长句/气口」子集试 `VAD_SILENCE_SEC=0.35` 对照 0.45**（看 device TTFA 与粘句率）。不要本轮改默认。
