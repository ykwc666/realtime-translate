# VAD_MIN_UTT_SEC A/B（2026-11-05）

防过拟合约束：本轮**未**改 `mt_postedit` / TM / `prefer_2pass_asr`。

## 语料

- 连续：`cache/harvest_public/ami_en2001b_1993_2020.wav`（~26s）
- 拼接压力：ledger `ami_006/014/018/031/039` + 0.35s 静音  
- 总长 ~46s（见 `docs/vad_min_utt_ab.json` → `audio`）

## 表：0 vs 1.0

| 指标 | `VAD_MIN=0` | `VAD_MIN=1.0` | Δ |
|------|-------------|---------------|---|
| n_utts | 5 | 5 | 0 |
| short_lt_1s / rate | 0 / 0% | 0 / 0% | — |
| func_end / rate | 0 / 0% | 0 / 0% | — |
| first_stable_mt p50 | 445.6 ms | 416.7 ms | **−28.9 ms** |
| latency_perceived p50 | 598.9 ms | 602.2 ms | +3.3 ms |
| device_ttfa p50 | n/a（无 TTS 播放） | n/a | — |

落盘：`docs/vad_min_utt_ab.json`、`docs/vad_min_utt_ab_detail.jsonl`。  
脚本：`scripts/ab_vad_min_utt.py`。

## 一句话结论

**默认保持 `VAD_MIN_UTT_SEC=0.0`。**  
依据：本表 **short_lt_1s 基线=0**（旋钮对 &lt;1s 碎句空转）；切点多为 ASR 复读/unchanged **强制收句**（`forced_trunc`，现行实现会绕过 min-utt hold）。延迟门（first_stable_mt p50 涨幅 ≤30ms）虽满足，但**无 fragment 净收益 → 不开默认**。

## 抽查呼应

`docs/ledger_review_15_notes.md`：ASR 锅成立（漏检 / 半句 / 碎片识别）。连续会上的碎句问题更多来自强切与识别质量，不是单纯「&lt;1s VAD 切段」——下一步若要再动切句，应针对 **forced_trunc 短句是否并入** 单独 A/B，而不是先把 min-utt 默认拧到 1.0。

## A5 备注（只记不改）

Oneshot：AMI offline WER 0.16 &lt; merged 0.20。若日后允许改 `prefer_2pass_asr`，应更偏 offline；**本轮仍不改**。
