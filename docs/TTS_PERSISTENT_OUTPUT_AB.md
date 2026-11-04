# TTS 真实首音与持久 OutputStream A/B

> 日期：2026-11-04  
> 任务：`docs/CURSOR_TASK_TTS_OUTPUT_STREAM.md`  
> 复现：`.\.venv\Scripts\python.exe scripts\bench_tts_stream_ab.py --reps 5 --persistent-ab`  
> 明细：`docs/tts_stream_ab.jsonl`

## 结论（有没有用）

**有用，默认开启 `TTS_PERSISTENT_OUTPUT = True`。**

原先 speak_wall 减去 synth 与 `play_sec×1000` 后每句仍剩约 **150–380ms**，主要不是合成，而是：

| 成分 | oneshot 中位数 | 说明 |
|---|---:|---|
| `stream_open_ms` | **~29ms** | 每句新建 `OutputStream` |
| `pcm_to_cb_ms` (`ttfa_callback − ttfa_pcm`) | **~75ms** | PCM 就绪→设备回调写出非零 |
| `close_or_drain_ms` | **~219ms** | 末样本写出→`play` 返回（关流/排空） |
| open+drain（每句额外开销） | **~248ms** | 远超任务 80ms 门槛 |

`ttfa_ms` / `ttfa_pcm_ms` 仍是「首段 PCM 就绪」，**不是**耳机真正出声；真实设备首音看 `ttfa_callback_ms`。

## Phase1 门槛

任务：仅当「每句额外开销 &lt;80 **且** pcm→callback &lt;80」才跳过 Phase2。

实测（oneshot / stream）：额外开销 ~248ms ≥80 → **进入 Phase2**（即便单独看 open≈29、pcm→cb≈75）。

## Phase2 A/B（stream 模式，reps=5，真实扬声器）

| 句子 | open_on | open_p | cb_on | cb_p | wall_on | wall_p |
|---|---:|---:|---:|---:|---:|---:|
| Hello. | 24.3 | 0.0 | 116.9 | 59.7 | 1241 | 910 |
| How's the weather today? | 26.5 | 0.0 | 152.9 | 80.0 | 1876 | 1510 |
| I might not pass this interview… | 29.5 | 0.0 | 196.5 | 130.3 | 2648 | 2350 |
| Hang on a second… | 28.1 | 0.0 | 144.6 | 80.0 | 4378 | 4070 |
| Okay, please say that again… | 32.9 | 0.0 | 152.5 | 60.1 | 4235 | 3820 |
| 稍等一下，我马上回来。 | 26.6 | 0.0 | 189.9 | 159.9 | 2559 | 2430 |
| 延迟大概有多少 | 29.0 | 0.0 | 182.3 | 109.7 | 2278 | 1930 |
| 我可能过不了这一面 | 33.1 | 0.0 | 216.1 | 129.9 | 2268 | 1980 |

汇总（oneshot − persistent，越大=持久更好）：

- `stream_open` 中位 Δ **+28.6ms**
- `ttfa_callback` 中位 Δ **+69.4ms**
- `speak_wall` 中位 Δ **+319ms**（主要来自 drain≈0）
- underrun：双方 **0**
- 打断：persistent `stop()` ~15ms 量级，不调用全局 `sd.stop()`

## 默认与回退

```python
TTS_PERSISTENT_OUTPUT = True   # 持久 OutputStream；False 完全回退每句新建
TTS_STREAM_PLAY = True         # 子句流水线收益保留
```

关：`TTS_PERSISTENT_OUTPUT=False` 或环境/配置覆盖。

## DualTrackStats.t_first_audio_ms

**仍不能**当真实耳机首音：队列侧继续用 `TtsResult.ttfa_ms`（= `ttfa_pcm_ms`）。  
要接真实首音：把 `play_timing.ttfa_callback_ms` 经 `tts_queue` 写入 stats（本任务未改 ASR/双轨统计路径）。

## 人工试听清单

1. 连续两句英文：无上一句残留、无串句。  
2. 尾音节（如 Tuesday 的 -day）：tail pad 仍在。  
3. 说话打断 TTS：应迅速静音，下一句可再播。  
4. 多子句长句：子句 1 仍明显早于整句合成结束。

## 测试

```text
.\.venv\Scripts\python.exe -m pytest tests/test_tts_interrupt.py tests/test_tts_stream_ttfa.py tests/test_tts_play_timing.py -q
```
