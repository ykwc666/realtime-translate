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

### 2.1 当前基线（`275e432`，2026-11-12）— 以这张为准

**读数冻结**：见 [`KPI_FREEZE_DIALOGUE_V2.md`](KPI_FREEZE_DIALOGUE_V2.md)；git tag `dialogue-kpi-v2-freeze` → `275e432`。

上表是 V2 首跑存档。此后 `711800a`（en_zh 短句 TM/假名兜底）、`275e432`（标点吞尾守卫）动过 KPI，现值为：

| 集 | 方向 | n | asr_ok | mt_ok | mt_ok_given_asr (n) | empty_asr | fragment_lost | fragment_dup | asr_err |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Test v2** | zh_en | 60 | 0.850 | 0.783 | 0.843 (51) | 0 | 0 | 0.017 | 0.2329 |
| **Test v2** | en_zh | 60 | 0.783 | 0.883 | 0.957 (47) | 0 | 0.050 | 0 | 0.2309 |
| **Dev v2** | zh_en | 60 | 0.783 | 0.783 | 0.851 (47) | 0 | 0.033 | 0 | 0.2294 |
| **Dev v2** | en_zh | 60 | 0.767 | 0.750 | 0.870 (46) | 0.017 | 0.133 | 0 | 0.2267 |

变化来源：en_zh 的 `mt_ok` 0.817→0.883（Test）来自 `711800a` 而不是语料；zh_en Test `asr_ok` 0.833→0.850 来自 `275e432` 的吞尾回退（027 一条）。Dev en_zh `mt_ok` 46→45 是 §4.1 说的 1 条抖动（`en_zh_005`），不是回归。

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

### 4.1 读数注意（两条，写在这里防止以后误判）

1. **噪声地板 1 条**：2pass 投机解码在静音处快照 `self.buf`（`app/dual_track.py:632-660`），命中后 finalize 直接用该快照（`:1227-1234`），快照长度依赖线程调度。实证：`c6e6690` 只改观测字段（diff 全为写 `stats.*`/`print`，无分支改动），同两集重跑仍出现 Test en_zh `asr_err_mean` 0.2309→0.2323→0.2309、`coverage` 0.9684→0.9517、`incomplete_asr` 1→2、`fragment_lost` 2→3；Dev en_zh `coverage` 0.9084↔0.9167。**A/B 只认 ≥2 条的差**，1 条以内不得当结论。详见 `docs/OBS_FIX_REVIEW.md`。
   `mt_ok` 是 `src` 的下游，同样会抖：`275e432`（守卫只改展示层、Dev 触发 0 次）Dev en_zh `mt_ok` 46→45，唯一新增失败是 `en_zh_005`，其识别文本在两次跑之间从 "going to … because" 变成 "gonna … 'cause"（sem 仍判 ok）。口径：**zh_en 的 `mt_ok` 一条不许变；en_zh 允许 ±1，但必须指到具体某条 utt 的识别文本变化**。
2. **en_zh 短题不可比**：按参考句长度分桶，Test ≥9 词 27/27、≤2 词 11/15；Dev ≥9 词 22/24、≤2 词 11/16。1 词条目（`dying` / `Ohh.` / `for`）ASR 只有 0 或 1，无部分分，是题目缺陷不是能力缺陷。**报 en_zh 时必须同时给 ≥9 词那一档**，否则短题会把分数拉花。

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
