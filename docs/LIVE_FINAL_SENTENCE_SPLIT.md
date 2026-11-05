# A0：final 二次切分（MT/TTS 前）

日期：2026-11-05  
提交意图：`feat(live): split merged final sentences before MT/TTS`

## 行为

- 仅 **final** 路径：若待译文本含 ≥2 个句末标点（`。？！?!.;`），
  用 `retranscribe.split_sentences` 拆子句，**同一 Translator** 逐句 MT，
  `_enqueue_speak` 收子句列表（一句一播）。
- 裸 ASR 标点不足且 `ASR_PUNCT_DISPLAY` 开启时，先用标点模型补全再数/拆
  （应对 SenseVoice 把两句粘在一起、标点后才出现双句末的情况）。
- `stats.source` 仍为整段展示文本（不拆）；`stats.translated` 为子句译文空格连接。
- harvest / `live.jsonl` 增字段 `sub_lines: [{src,tgt},…]`（未拆则为 `null`）。
- **不动** partial、VAD 三件套、块长、ASR_LOOP/SPEC、TM。

## 验收

| 项 | 结果 |
|---|---|
| 并句 dualsim | `accept_phrases` 原文件在 TEN 0.4 下天气/星期二已被 VAD 拆开；用同源无缝拼接 `cache/ab_weekday/accept_phrases_weather_tue_merged.wav`（gitignore 下 cache）复现并句 → `sub_lines` 两条 |
| 证据对齐 | `src=今天天气怎么样？？现在是星期二。`，`sub_lines=[{天气…},{星期二…}]`，`tgt` 空格连接 |
| TTS | 同 wav 不开 `--no-tts`：`mt_triggers=2`，队列两段入队（一句一播） |
| 双跑 | 同 wav 连跑两次，`src|tgt|sub_lines` 逐字一致 |
| pytest | **121 passed** |
| eval_ledger `--skip-a5 --corpus ami,fleurs` | 见下 |

## Ledger（如实记录，不回退拆分）

| 语料 | 修前 pipeline ok | 修后 | WER |
|---|---|---|---|
| AMI | **27/48** | **26/48**（−1） | 0.4433（不变） |
| FLEURS | **11/12** | **11/12** | 0.1352（不变） |

AMI −1：英文 final 在标点补全后出现 ≥2 句末时也会拆开逐句 MT，个别条目 judge 从 ok→wrong；按任务书不回退逻辑。
