# 双人对话 KPI 基线 V2（T1/T2 配对过滤）— 2026-11-11

本轮**只修语料与测量**，不改识别/翻译行为。旧基线见 `DIALOGUE_BASELINE_MSLT.md`（已标注作废）。

Python：`3.12.0`（Windows，`.venv`）

## 1. 过滤账

先 `prefer-split`，再 T1↔T2 `pair_sim≥0.5`，再 `seed=11` shuffle 取 N。坏题不进池。

### Test（`data/dialogue_corpus_v2`）

| 方向 | 候选（prefer 后） | drop empty_tier | drop pair_mismatch | 保留 valid | 实抽 |
|---|---:|---:|---:|---:|---:|
| zh_en | 1285 | 160 | 414 | **711** | **60** |
| en_zh | 2127 | 15 | 58 | **2054** | **60** |

### Dev（`data/dialogue_corpus_dev_v2`）

| 方向 | 候选（prefer 后） | drop empty_tier | drop pair_mismatch | 保留 valid | 实抽 |
|---|---:|---:|---:|---:|---:|
| zh_en | 1256 | 255 | 343 | **658** | **60** |
| en_zh | 2223 | 8 | 44 | **2171** | **60** |

审计：同目录 `rejected.jsonl`。同 seed 重跑 manifest 的 `notes`（stem）列表一致。

## 2. 新基线表

| 集 | 方向 | n | asr_ok | mt_ok | mt_ok_given_asr (n) | empty_asr | fragment_lost | fragment_dup | pair_sim_p10 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Test v2** | zh_en | 60 | **0.833** | **0.783** | **0.860 (50)** | 0 | 0 | 0.017 | 0.744 |
| **Test v2** | en_zh | 60 | 0.767 | 0.817 | 0.891 (46) | 0 | 0.067 | 0 | 0.829 |
| **Dev v2** | zh_en | 60 | **0.783** | **0.783** | **0.851 (47)** | 0 | 0.033 | 0 | 0.817 |
| **Dev v2** | en_zh | 60 | 0.767 | 0.717 | 0.826 (46) | 0.017 | 0.133 | 0 | 0.918 |

`pair_sim_min`：Test zh_en 0.556 / en_zh 0.500；Dev zh_en 0.571 / en_zh 0.778（均 ≥0.5）。

## 3. 新旧对照（同一判定器）

| 指标（zh_en） | 旧 Test（未校验 T1/T2） | 新 Test v2 |
|---|---:|---:|
| n | 30 | 60 |
| asr_ok | 63.3% | **83.3%** |
| mt_ok | 70.0% | **78.3%** |
| mt_ok_given_asr | 100% (19) | 86.0% (50) |
| empty_asr | 0 | 0 |

一句话：差异来自**去掉「音频=T1、参考=另一句 T2」的坏题**，不是改了 ASR/MT 代码。旧 63% asr_ok 上限≈坏题比例；新集才是产品在被打分。

## 4. Holdout 结论（本次唯一问题）

去掉坏题后：

- Test zh_en `mt_ok` = **78.3%**
- Dev zh_en `mt_ok` = **78.3%**
- **差 = 0.0 pp**

旧 holdout（坏题集）Test 70% vs Dev 46.7% 的鸿沟，主要是语料污染，不是「补丁只对 Test 有效」。given-ASR 两侧仍高（86% / 85%）。

## 5. 复现命令

```bat
.\.venv\Scripts\python.exe scripts\import_mslt_dialogue.py --zip data\dialogue_corpus\MSLT_Corpus.zip --out data\dialogue_corpus_v2 --prefer-split Test --seed 11 --n-zh-en 60 --n-en-zh 60
.\.venv\Scripts\python.exe scripts\import_mslt_dialogue.py --zip data\dialogue_corpus\MSLT_Corpus.zip --out data\dialogue_corpus_dev_v2 --prefer-split Dev --seed 11 --n-zh-en 60 --n-en-zh 60
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_v2\manifest.jsonl --out-dir data\eval\dialogue_v2
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_dev_v2\manifest.jsonl --out-dir data\eval\dialogue_dev_v2
.\.venv\Scripts\python.exe -m pytest -q
```

明细：`data/eval/dialogue_v2/`、`data/eval/dialogue_dev_v2/`（gitignore）。

## 验收自检

| # | 项 | 结果 |
|---|---|---|
| A | min(pair_sim)≥0.5，无空 src_ref | **过** |
| B | 各方向 ≥50 | **过**（60/60） |
| C | 同 seed id/notes 一致 | **过** |
| D | 本轮未改 VAD/MIC_DENOISE/判定口径；无 TM/postedit/ASR 权重调参 | **过**（仅 mock 兼容回退 `transcribe_with_2pass_parts`） |
| E | pytest -q | **157 passed** |
| F | 本报告 5 节齐全 | **过** |
