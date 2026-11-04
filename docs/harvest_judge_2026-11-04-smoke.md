# Harvest 人判表 — 2026-11-04-smoke

来源采集：`data/harvest/2026-11-04-smoke.jsonl`
生成时间：2026-11-04 18:58:49

## 判定标准（两次会话必须同一尺子）

| 档 | 含义 |
|----|------|
| **ok** | 与 src 语义等价，口语自然，可直接播给中文听众 |
| **awkward** | 能懂，但不自然 / 术语别扭 / 语序怪（听众要愣一下） |
| **wrong** | 语义错、极性反、关键信息丢、或听不懂 |

- 只看 **tgt**（发给 TTS 的最终译文）相对 **src**。
- `mt_raw` 仅用于分组：`mt_raw != tgt` → 规则层/TM 修过；相等 → 模型原样出。
- 不要用字符串相似度自动判；本文件人判列必须手填。


## 待判表

| # | src | mt_raw | tgt | rule_touched | verdict |
|---|-----|--------|-----|--------------|---------|
| 0 | 你好，延迟大概有多少？ | How much delay is it about? | How much delay is it about? | no |  |
| 1 | 今天天气怎么样？ | How's the weather today? | How's the weather today? | no |  |
| 2 | 现在是星期二。 | It's Tuesday. | It's Tuesday. | no |  |
| 3 | 他现在没有回我的消息。 | He hasn't replied to my messages yet. | He hasn't replied to my messages yet. | no |  |

## 填判方式

1. 编辑 `D:/xuanjian/realtime-translate/docs/harvest_judge_2026-11-04-smoke.jsonl`，把每行 `verdict` 填成 `ok` / `awkward` / `wrong`。
2. 汇总：`python scripts/judge_harvest.py --ingest D:/xuanjian/realtime-translate/docs/harvest_judge_2026-11-04-smoke.jsonl`

