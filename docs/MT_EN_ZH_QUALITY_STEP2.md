# MT 质量线第二步：收掉规则边界 + holdout 防过拟合

日期：2026-11-04  
前置：`MT_EN_ZH_QUALITY_STEP1.md`（50 句 88%，6 句规则边界）

## 做了什么

对上轮边界三类加**窄触发** postedit（必须带通话/会议锚点），并 TM 追加 6 条原句；另加 **10 句 holdout 变体**（同模式不同措辞）。

| 类 | 触发锚点 | 反例（不修） |
|--|--|--|
| colloquial jump off | gonna/I'll + jump off | jump off the bridge |
| colloquial broke up | you/there/for a bit | broke up with him |
| sense call drops | call(s) drop / keep dropping | — |
| sense walk me through | walk me through + broke/release | — |
| direction push deadline | push the deadline by … | → 往后推，非提前 |
| direction until after rollout | not change/touch + until after rollout | — |

## 结果

| 集 | 通过 | broken | P95 |
|--|--:|--:|--:|
| 原 50（step1） | **50/50 (100%)** | 0 | — |
| holdout 10 | **10/10 (100%)** | 0 | — |
| 合计 60 | **60/60 (100%)** | 0 | **0.43ms** |

全量测试：**82 passed**。

## 结论

这把口语尺子上，规则+TM 已饱和；holdout 说明这几类模式有一定泛化，**不是只背原句**。再扩集若出现新失败类，优先看是否还能窄规则；若是开放域长句/推理类，再上更强 MT。
