# VAD_SILENCE_SEC 0.45 vs 0.35（paced + 真实 TTS）

> 日期：2026-11-04  
> 条件：Silero、`TTS_PERSISTENT_OUTPUT=True`、`--tts on`、其余默认不变  
> 复现：`.\.venv\Scripts\python.exe scripts\bench_paced.py --wav <wav> --tts on --persistent on --silence 0.35|0.45`

## 结论（有没有用）

**有用（延迟），本轮不改默认。**

- device TTFA / mt_ready 几乎各少 **~100ms**（静音门槛 0.45→0.35 的账本平移）。  
- 本批合成 wav **句数与分段一致，未见粘句**。  
- 历史顾虑是**实麦气口**（短停被切）；合成夹具盖不住，故 **默认仍 `VAD_SILENCE_SEC=0.45`**。要改默认需真人气口试听。

## 主表（device_ttfa / mt_ready 中位，ms）

| wav | n@0.45 | n@0.35 | device 0.45→0.35 | mt_ready 0.45→0.35 | 粘句？ |
|---|---:|---:|---|---|---|
| accept_phrases | 4 | 4 | **609 → 538 (−71)** | 450 → 368 | 否（文本同） |
| tail_duosimo | 2 | 2 | **583 → 493 (−90)** | 452 → 351 | 否 |
| checklist_live_like | 7 | 7 | **592 → 499 (−93)** | 450 → 350 | 否（7=7） |
| pause_xingqi_er | 1 | 1 | 967 → 853 (−114) | 851 → 744 | 两边都在「星期啊」收（ASR/仲裁，非粘句） |

## 延迟账本

`cut_ms` 从 ~450 落到 ~350；投机命中时 ASR/MT 仍贴 cut。  
device TTFA ≈ cut +（PCM+设备）。砍静音是当前最大单刀，TTS 持久流已是次要项。

## 默认

```python
VAD_SILENCE_SEC = 0.45  # 保持；可用 --silence 0.35 试体感
```
