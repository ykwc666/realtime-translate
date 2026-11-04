# 公开匿名语料 harvest（2026-11-04）

你无需再播 loopback。本轮用 CC-BY 公开语音（经 hf-mirror）拼成 `cache/harvest_public/public_en_bundle.wav`（约 5 分钟：AMI 会议片段 + FLEURS×3 + LibriSpeech 节选 240s），跑进本机 ASR→MT 采集。

来源清单：`cache/harvest_public/SOURCES.json`（AMI / FLEURS / LibriSpeech，CC-BY-4.0）。

## 结果（人判 tgt vs ASR src）

| 档 | n | 占比 |
|--|--:|--:|
| ok | 3 | 6% |
| awkward | 19 | 38% |
| wrong | 28 | 56% |

| 分组 | n | wrong% |
|--|--:|--:|
| **rule_touched**（mt_raw≠tgt） | **0** | — |
| model_passthrough | 50 | 56% |

延迟（`--no-tts`）：perceived P50 **922ms** / P90 **1141** / max **1334**；device_ttfa=null。

## 解读

1. **规则层在真实分布上覆盖率 ≈ 0**：自写通话尺子上修过的句式，这批会议/朗读 ASR 几乎碰不到。  
2. **质量崩盘主因是 ASR 错/截断 → MT 跟着错**，不全是翻译模型本身；文学朗读 + 会议口音对当前 Paraformer 很伤。  
3. 下一步：先改善真实语音上的 ASR（或换更贴会议的模型），再谈 MT；继续堆 call-domain postedit 对这批语料收益接近零。

产物：`data/harvest/2026-11-04-public-en.jsonl`、`docs/harvest_judge_2026-11-04-public-en.*`、`docs/harvest_summary_2026-11-04-public-en.json`。
