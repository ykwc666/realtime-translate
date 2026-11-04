# 优化对照索引（每次改动后更新）

机器：Ryzen 7 4800H / 32GB / 无 NVIDIA。  
主日志：[`OPTIMIZATION_LOG.md`](./OPTIMIZATION_LOG.md)。  
可复现 JSONL / A/B：下表。**对比时看同口径字段**（`mt_ready`≠`device_ttfa`；`asr_wait`=cut→asr_done）。

| 日期 | 主题 | 默认是否上线 | 关键数字 | 证据 |
|------|------|--------------|----------|------|
| 2026-11-04 | 2pass 并行 3+2 | 是 | perceived 622→458 | `ASR_2PASS_PARALLEL_AB.md` |
| 2026-11-04 | 投机提前量自适应 | 是 | 长句 978→604 | `SPECULATE_ADAPTIVE_LEAD_AB.md` |
| 2026-11-04 | finalize 不堵采集 | 是 | utt_wall 2836→713 | `FINALIZE_WAIT_TTS_AB.md` |
| 2026-11-04 | TTS 子句流水线 | 是 | 多句 TTFA −140ms | `TTS_STREAM_AB.md` |
| 2026-11-04 | TTS 持久流 | 是 | callback −69 / wall −319 | `TTS_PERSISTENT_OUTPUT_AB.md` |
| 2026-11-04 | device TTFA 账本 | 是（度量） | accept ~619–653 | `END_TO_END_TTFA_AB.md` |
| 2026-11-04 | VAD 0.35 | **否**（仍 0.45） | device −70~−93 | `VAD_SILENCE_035_AB.md` |
| 2026-11-04 | early-cut 就绪即切 | **否** | safe_350=0% | `ENDPOINT_EARLY_CUT_AB.md` |
| 2026-11-04 | re-arm 机制 | 是 | miss_grow→hit | `SPEC_REARM.md` |
| 2026-11-04 | re-arm offline 第二枪 | 是 | pause wait 352→193 vs full | `SPEC_REARM_OFFLINE.md` |
| 2026-11-04 | 标点去白名单 | **否**（展示 only） | 探针失败 | `PUNCT_ENDPOINT_PROBE.md` |
| 2026-11-04 | GTCRN 降噪 | **否**（可选） | 未实麦 A/B | `MIC_DENOISE.md` |
| 2026-11-04 | 精确 TM | 是 | hit ~0.01ms | `mt_tm_ab.jsonl` |
| 2026-11-04 | ONLINE_TAIL_PAD 0.5 | 是 | pause wait 230→143 | `TAIL_PAD_AB.md` |
| 2026-11-04 | en→zh 质量尺子+高频修 | 是 | 通过 42%→88%，broken 0 | `MT_EN_ZH_QUALITY_STEP1.md` |
| 2026-11-04 | en→zh 边界类+holdout | 是 | 60/60，broken 0，P95 0.43ms | `MT_EN_ZH_QUALITY_STEP2.md` |

## 检查点约定

1. 每完成一轮可复现 A/B：**先写/改本表 + OPTIMIZATION_LOG，再 `git commit`**。  
2. 合成对照优先：`cache/ab_weekday/*.wav` + `scripts/bench_paced.py` / `bench_spec_rearm_*.py`。  
3. 不上线的项在表中标明 **否**，避免误当默认收益。
