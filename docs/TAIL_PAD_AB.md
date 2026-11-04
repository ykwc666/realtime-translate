# ONLINE_TAIL_PAD_SEC A/B（2026-11-04）

动机：re-arm offline 命中后 CUT 仍要 `finalize_online_only`，1.0s 零 pad 解码是 asr_wait 大头之一。

## 结果（silence=0.45，rearm=offline，无 TTS，1 rep）

| wav | pad | asr_wait_med | mt_ready_med | 相对 1.0 文本 |
|-----|-----|--------------|--------------|---------------|
| pause_xingqi_er | 1.0 | 230 | 375 | ref |
| pause_xingqi_er | **0.5** | **143** | **288** | 一致 |
| pause_xingqi_er | 0.25 | 239 | 396 | 一致但不稳 |
| accept_phrases | 1.0/0.5/0.25 | ~0.2 | ~450 | 一致 |
| cont_tuesday | 同左 | ~0.2 | ~450 | 一致 |
| tail_duosimo | 1.0 | 1.7 | 452 | ref |
| tail_duosimo | 0.5 | 0.1 | 450 | 一致 |
| tail_duosimo | 0.25 | 58.6 | 509 | 一致但 wait↑ |

## 决策

**默认 `ONLINE_TAIL_PAD_SEC=0.5`。** 合成集无末字回退；pause 气口句 wait/mt_ready 明显更好。0.25 不升。

原始：`docs/tail_pad_ab.jsonl`；复现：`scripts/bench_tail_pad_ab.py`。
