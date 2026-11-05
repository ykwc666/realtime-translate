# A0：final 二次切分（MT/TTS 前）

日期：2026-11-05  
相关提交：
- `77c37fe` 初版（展示标点参与切分决策 → AMI 27→26）
- 本修：切分只看 **ASR 原生标点**；`.` 数字/缩写保护

## 行为

- 仅 **final**：若 ASR 文本存在**不在串尾**的句末标点，且其后内容 ≥4 汉字或 ≥3 英文词，
  则用 `retranscribe.split_sentences` 拆子句，同一 Translator 逐句 MT，
  `_enqueue_speak` 收列表（一句一播）。
- 句末集合：`。？！?!`；`.` 单独规则（非 `10.5`；非 `a.m.`/`U.S.`/`Mr.`；
  串尾或后接空白+大写/汉字才算）。
- **不**用标点模型做切分决策；`add_punctuation` 只在 `_finalize_utt` 展示用，**每 final 一次**。
- `stats.source` 不拆；`stats.translated` 子句译文空格连接；`sub_lines` 有则写出。
- **不动** partial、VAD 三件套、块长、ASR_LOOP/SPEC、TM。

## 验收（本修后）

| 项 | 结果 |
|---|---|
| 并句 dualsim | `accept_phrases_weather_tue_merged.wav` → ASR `…怎么样？现在是星期二` → 2×MT，`sub_lines` 两条；`[punct …]` 日志 1 次 |
| ami_008 回归 | 无标点英文不拆 |
| pytest | 全绿 |
| eval_ledger `--skip-a5 --corpus ami,fleurs` | AMI **27/48**、FLEURS **11/12**；WER 0.4433 / 0.1352 |

## Ledger 历程

| 阶段 | AMI pipeline ok | FLEURS |
|---|---|---|
| A0 前 / 本修后 | **27/48** | **11/12** |
| A0 初版（标点模型参与决策） | 26/48 | 11/12 |
