# ASR 2pass 合并策略 A/B（2026-11-05）

不换模型；只改 `prefer_2pass_asr` 选哪路文本。  
落盘：`docs/asr_2pass_merge_ab.json`；脚本：`scripts/ab_2pass_merge.py`。

## 表（oneshot，ledger 官方 ref）

| mode | AMI WER | FLEURS WER | main (ami+fleurs) | offline 选用率 |
|------|---------|------------|-------------------|----------------|
| **legacy**（旧默认） | **0.200** | 0.147 | 0.190 | 0.81 |
| **offline** | **0.157** | **0.074** | **0.140** | 1.00 |
| offline_conflict | 0.157 | 0.074 | 0.140 | 1.00 |

## 合成集抽查

`docs/asr_2pass_merge_synth.json`：accept / tail 关键句仍对；`pause_xingqi_er` 在 **legacy 下已是**「星期啊」（旧启发式冲突分支也会选 offline），offline 模式不额外恶化。

## 默认

`ASR_2PASS_MERGE_MODE = "offline"`  

依据：上表 AMI 列 0.200→0.157（约 −4.4 pp），FLEURS 同步变好。  
`merge_overlap_text` / SenseVoice 增量路径未改。

## 风险备注

SenseVoice offline 偶发口语幻觉（如「星期三」→「星期啊」）已用 `prefer_2pass_asr` 内 **weekday 保护** 挡住：online 已是完整「星期X」且 offline 不是时，信 online（各 merge mode 均生效）。

## 流水线复测（紧闸门，AMI+FLEURS，`--skip-a5`）

对照：`docs/ledger_eval_summary_before_offline_merge.json` → `docs/ledger_eval_summary_after_offline_merge.json`  
日志：`docs/ledger_eval_offline_merge_verify.log`

| 指标 | before (legacy 合并语义下的分账基线) | after (`merge=offline`) |
|------|--------------------------------------|-------------------------|
| AMI pipeline ok | 18/48 = **37.5%** | 20/48 = **41.7%** |
| FLEURS pipeline ok | 8/12 = **66.7%** | 9/12 = **75.0%** |
| AMI WER mean | 0.609 | 0.592 |
| FLEURS WER mean | 0.324 | 0.242 |
| mt_only ok | AMI 93.8% / FLEURS 91.7% | 不变 |

净增益：翻到 ok +5，掉出 ok −2（ami_019→awkward，ami_042→wrong）。  
结论：**默认 `offline` 保留**；缺口仍在 ASR（mt_only ≈94%），下一步优先低 RMS / 截断 / weekday 边角，不回头改双轨合并。
