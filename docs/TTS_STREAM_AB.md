# TTS 子句流水线（边合成边播）A/B

> 日期：2026-11-04  
> 复现：`python scripts/bench_tts_stream_ab.py --reps 3`  
> 明细：`docs/tts_stream_ab.jsonl`

## 发现

当前 Piper medium **一次 `synthesize()` 只吐 1 个大块**（整句算完才返回），所以按「样本 preroll」假流式**没有收益**，还会因线程开销略慢。

真正有效的是 **按标点切子句**：先合成并播放句 1，同时合成句 2+。

## 主指标：TTFA（停说后→耳机可出声）

| 句子 | batch TTFA | clause-stream TTFA | Δ（越大越好） |
|---|---:|---:|---:|
| Hello. | 53 | 47 | +6 |
| How's the weather today? | 92 | 82 | +11 |
| I might not pass this interview round. | 125 | 113 | +12 |
| **Hang on a second, I'll be right back…** | **209** | **67** | **+142** |
| **Okay, please say that again, and…** | **191** | **49** | **+142** |
| 稍等一下，我马上回来。 | 152 | 168 | −15 |
| 延迟大概有多少 | 104 | 105 | −1 |
| 我可能过不了这一面 | 116 | 101 | +15 |

- **多子句英文**：首音约 **−140 ms**（209→67 / 191→49）
- **单句短文本**：接近打平（Piper 本来就一块）
- 总播报墙钟不变（音频一样长）；砍的是**等到听见的时间**

## 默认

```python
TTS_STREAM_PLAY = True   # 走子句流水线；单句自动回退整句合成
```

单测：`tests/test_tts_stream_ttfa.py`（多子句 TTFA &lt; 0.75× batch）。
