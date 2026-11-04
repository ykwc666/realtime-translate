# P0-3：finalize 不阻塞采集线程（FINALIZE_WAIT_TTS）

> 日期：2026-11-04  
> 复现：`python scripts/bench_finalize_wait_ab.py`  
> 明细：`docs/finalize_wait_ab.jsonl`

## 改动

- `dual_track._finalize_utt`：`FINALIZE_WAIT_TTS=False` 时**不再** `tts_q.wait_idle(60)`
- `tts_queue`：在该模式下于 unmute 前 `sleep(PLAYBACK_MUTE_PAD_SEC)`（尾音垫到 TTS 线程）
- `pipeline`：采集线程跳过 post-utt mute pad（避免播报未完就 unblock 又 sleep）

## A/B（`tail_duosimo.wav` + 真实 Piper 播放）

| | wait=on（旧） | wait=off（新） |
|---|---:|---:|
| utt_wall median | **2836 ms** | **713 ms** |
| feed 返回 median | **2516 ms** | **345 ms** |
| perceived（句1/句2） | 976 / 611 | 968 / 621 |
| src 文本 | 延迟… / 今天天气… | 相同 |

**结论：** perceived（停说→译文就绪）几乎不变；**采集线程提前约 2s 回到**，可在播报时继续听下一句 / 插话。`utt_wall` 不再虚高含播放时长。

## 默认

```python
FINALIZE_WAIT_TTS = False
```

附：`tests/test_prefer_2pass_asr.py`（P2-9 启发式补测）。
