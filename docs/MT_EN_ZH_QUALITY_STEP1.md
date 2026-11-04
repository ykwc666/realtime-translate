# MT 质量线第一步：en→zh 评测扩集 + 高频失败修复

日期：2026-11-04  
范围：仅 MT（TM / ct2 / postedit）；未动 ASR / VAD / TTS / 端点 / 投机。

## 1. 评测集

- `data/mt_call_eval_en_zh.jsonl`：10 → **50**（原 10 句原样保留，追加 40）
- 人工参考译文；`tags` 多标：question / number / negation / colloquial / meeting / domain / long
- 覆盖：每类 ≥5

## 2. 基线（修复前）

判定：人脑语义等价（`docs/mt_en_zh_judge.jsonl`）；raw = ct2 裸译（绕开 TM）。

| | ok | fixed | wrong | broken | 通过(ok+fixed) |
|--|--:|--:|--:|--:|--:|
| 基线 | 15 | 6 | 27 | 2 | **21/50 (42%)** |

postedit P50/P95：**0.026 / 0.380 ms**

### 基线按覆盖类（通过率）

| tag | n | pass% |
|--|--:|--:|
| legacy | 10 | 100% |
| domain | 16 | 31% |
| meeting | 19 | 21% |
| colloquial | 9 | 11% |
| question | 10 | 20% |
| number | 11 | 36% |
| negation | 10 | 20% |
| long | 8 | 25% |

### 失败类别（基线 wrong+broken）

| 类 | 次数 | 处置 |
|--|--:|--|
| meeting_term | 6 | **已修**（postedit） |
| domain_term | 6 | **已修** |
| term_literal | 4 | **已修**（ship/build） |
| meeting_idiom | 3 | **已修** |
| postedit_regression | 2 | **已修**（收窄 hear 规则） |
| noun_mistranslation (mute) | 2 | **顺带修**（为 ≥85% 硬门；规则便宜） |
| sense_loss | 2 | **规则边界** |
| colloquial_literal | 2 | **规则边界**（&lt;3） |
| direction_reverse | 2 | **规则边界**（需理解 until/push 方向） |

## 3. 修复后

| | ok | fixed | wrong | broken | 通过 |
|--|--:|--:|--:|--:|--:|
| 修复后 | 17 | 27 | 6 | **0** | **44/50 (88%)** |

postedit P50/P95：**0.069 / 0.431 ms**（&lt;10ms）

### 修复后按覆盖类

| tag | n | pass% |
|--|--:|--:|
| legacy | 10 | 100% |
| domain | 16 | 88% |
| meeting | 19 | 84% |
| colloquial | 9 | 78% |
| question | 10 | 80% |
| number | 11 | 91% |
| negation | 10 | 90% |
| long | 8 | 75% |

## 4. 硬门核对

| 门 | 结果 |
|--|--|
| broken = 0 | ✅ |
| 原 10 句 en→zh 不回退 | ✅ legacy 100% |
| 原 22 句 zh→en | ✅ `bench_mt_call_quality` base+pe 仍无 oneside/heard 回退 |
| postedit P95 &lt; 10ms | ✅ 0.43ms |
| en→zh 通过率 ≥ 85% | ✅ 88% |
| 每类单测 | ✅ `tests/test_mt_postedit.py` |
| 测试总数 ≥ 76 | 见 pytest |

## 5. 规则边界（未硬修）

1. **sense_loss**：call drops / walk me through — 需整句理解，非整词替换。
2. **direction_reverse**：push deadline（提前≠延后）、until after rollout 时间锚点反了。
3. **colloquial_literal**（&lt;3）：jump off、broke up（通话断线≠分手）。

## 6. 一句话结论

会议术语/领域词/字面义这几类靠 postedit+窄规则还能再抠一点，但 **direction_reverse / sense_loss 已到规则收益尽头**；下一刀若还要冲 95%+，应上更强 MT（领域微调 / 更大模型），而不是继续堆脆弱整句替换。

## 产物

- `docs/mt_en_zh_baseline_dump.jsonl` — 逐句 raw/pe/耗时
- `docs/mt_en_zh_baseline_summary.json` — 修复前汇总
- `docs/mt_en_zh_judge.jsonl` — 人判
- `docs/mt_en_zh_quality_summary.json` — 修复后汇总
- `scripts/bench_mt_en_zh_postedit.py` — dump + `--judge`
