# 降噪 A/B（GTCRN）— 2026-11-11

**结论：否决。** `MIC_DENOISE` 保持 `False`。不接线。

触发数字（预登记规则）：
- AMI ok **20/48 ≤ 27/48**
- AMI WER **0.4937 ≥ 0.4433**
- FLEURS ok **8/12 ≤ 10/12**

机制一句话：GTCRN 不适用于本机这批真实会议/朗读切片——常把有效语音抹掉或畸变（空识别↑、WER↑）；远场路线应换方向（远端降噪 / 更强 ASR），**不再重复这条路**。

AMI / FLEURS 为**远期参考，非当前 KPI**。当前 KPI 是双人对话（MSLT），本报告单独报其音量比。

## 1. 两臂对照（ledger pipeline）

| 语料 | 臂 | ok | ok_rate | WER | mt_only ok | empty_src |
|---|---|---:|---:|---:|---:|---:|
| AMI（远期参考） | raw | **27/48** | 0.5625 | **0.4433** | 45/48 | 9 |
| AMI（远期参考） | dn | **20/48** | 0.4167 | **0.4937** | 45/48 | 14 |
| FLEURS | raw | **11/12** | 0.9167 | **0.1352** | 11/12 | — |
| FLEURS | dn | **8/12** | 0.6667 | **0.3184** | 11/12 | — |

臂 1 复核基线：AMI 27/48 · 0.4433；FLEURS 11/12 · 0.1352；mt_only 45/48 → **C-1 PASS**。

复现：

```bat
.venv\Scripts\python.exe scripts\build_denoise_corpus.py
.venv\Scripts\python.exe scripts\eval_ledger.py --corpus ami,fleurs --skip-a5 --out-dir data\eval\denoise_ab\raw
.venv\Scripts\python.exe scripts\eval_ledger.py --manifest data\eval\denoise_ab\manifest.jsonl --corpus ami,fleurs --skip-a5 --out-dir data\eval\denoise_ab\dn_out
```

## 2. 音量比 RMS(dn)/RMS(raw)

### AMI+FLEURS（60）

| p10 | p50 | p90 | `<0.3` |
|---:|---:|---:|---:|
| 0.872 | 0.942 | 0.970 | **1/60**（ami_010） |

### 对话 KPI 集 MSLT（60，仅音量比，非 ok/WER）

| p10 | p50 | p90 | `<0.3` |
|---:|---:|---:|---:|
| 0.922 | 0.983 | 0.993 | **0/60** |

对话集电平几乎不削，但 AMI/FLEURS 识别仍变差 → 问题不只是「音量被削」，还有频谱畸变/切帧效应。

## 3. 逐条样例（ref / raw ASR / dn ASR）

| 类 | id | ΔWER | ref | raw | dn |
|---|---|---:|---|---|---|
| 变差 | ami_014 | +1.00 | THAT'S A GOOD POINT | That's a good point。 | _(空)_ |
| 变差 | fleurs_000 | +1.00 | Popular sports include… | Popular sports include football… | _(空)_ |
| 变好 | ami_010 | −0.88 | SO WHAT DO WE NEED TO TALK ABOUT | Yeah。 | What do we need to talk about？ |
| 变好 | ami_012 | −0.53 | PROBABLY JUST TWO OR… | _(空)_ | or three of whatever… |
| 无变化 | ami_004 | 0 | YOU SAW IT YEAH | Yeah。 | Yeah。 |

偶发变好不足以抵消整体 ok↓ / WER↑ / 空识别↑。

## 4. 闸门摘录

| 闸门 | 结果 |
|---|---|
| A-1 GTCRN online + abort if disabled | PASS（日志有 `frame=256`） |
| A-2 60 wav 时长偏差 ≤0.05s | PASS |
| A-3 manifest 60 行仅 path 变 | PASS |
| A-4 RMS 分位已打印 | PASS |
| C-1 臂 1 复现基线 | PASS |
| 预登记结论 | **否决** |
| `MIC_DENOISE` 默认 | **仍为 False** |
