# 可分账评测报告（真实语音）

> ⚠️ **本文是 2026-11-04 的快照，数字已过时。**
> 当时卡在「大量句子 ASR 全空」，AMI pipeline 通过率只有 8.3%、WER 0.917。
> 空 ASR 与切句修复后，当前数字（2026-11-06 复跑，判定零翻转）：
> **AMI 27/48 = 56.25%，WER 0.4433；FLEURS 11/12 = 91.67%，WER 0.1352；MT-only 45/48。**
> 以 `docs/ledger_eval_summary.json` 为准；分层与归因见 `docs/BOOT_TIMING.md` 同目录的
> `docs/EN_ZH_ACCURACY.md`。下文表格仅作历史对照，**不要再引用其中的 8.3% / 0.917**。

日期：2026-11-04  
判定口径：全部为 **model-judge**（非人工）。落盘可复算：`docs/ledger_eval_*.json*`、`docs/ledger_asr_oneshot*.json*`、`docs/mt_decode_ab*.json*`、`docs/ami_cut_diagnosis.json`、`docs/ledger_review_15.jsonl`。

语料：`data/ledger_corpus/manifest.jsonl` — **ami 48 + fleurs 12 + libri 4（OOD）**，全部带官方 `ref`（WER 覆盖率 100%）。

---

## 一句话结论

**主集（AMI/FLEURS）上责任主要在 ASR（含 VAD 切句），不是 MT。**  
AMI：pipeline ok **8.3%** vs MT-only（ref→MT）ok **95.8%** → 差距 ≈ ASR+切句贡献。  
先修：**会议域 ASR / 切句**；MT 侧已用 greedy + `no_repeat_ngram=3` + `repetition_penalty=1.1` 压重复（beam 不动）。

---

## 表① 按 corpus 的 WER

### Pipeline（VAD→ASR→MT，含未检出）

| corpus | n | WER 覆盖 | WER mean | 备注 |
|--------|---|----------|----------|------|
| ami | 48 | 48/48 | **0.917** | 39/64 全局空 ASR 拉高；检出子集 ≈0.75 |
| fleurs | 12 | 12/12 | **0.738** | 检出子集 ≈0.37 |
| libri (OOD) | 4 | 4/4 | **1.215** | 不计入主指标 |

来源：`docs/ledger_eval_summary.json` → `table_wer`。

### Oneshot（无 VAD，整段送 ASR）— 更干净的 ASR 尺子

| corpus | n | online WER | offline WER | merged WER |
|--------|---|------------|-------------|------------|
| ami | 48 | 0.548 | **0.157** | 0.200 |
| fleurs | 12 | 0.317 | **0.074** | 0.147 |
| libri (OOD) | 4 | 1.174 | 1.149 | 1.220 |

来源：`docs/ledger_asr_oneshot.json`。  
**解读**：SenseVoice offline 明显强于流式 Paraformer；现行 `prefer_2pass` 合并略差于纯 offline（AMI 0.200 vs 0.157）。A5 只测不改合并逻辑。

---

## 表② fragment_rate

来源：pipeline 实际切出的 30 句（`table_fragment`）。

| 指标 | 值 |
|------|----|
| n_utts | 30 |
| 时长 &lt; 1.0s 占比 | **0.0%**（本轮是预切单句 wav，短碎主要来自连续会话） |
| src 以功能词结尾占比 | **10.0%**（3/30） |

连续 AMI 片段诊断见 Phase C。`VAD_MIN_UTT_SEC` 已接线，**默认仍 0**（缺连续音 fragment A/B，不改默认）。

---

## 表③ 分账（pipeline vs MT-only）

| corpus | pipeline ok_rate | MT-only ok_rate | 差（≈ASR 贡献） |
|--------|------------------|-----------------|----------------|
| **ami** | **8.3%** (4/48) | **95.8%** (46/48) | **−87.5 pp** |
| **fleurs** | **33.3%** (4/12) | **100%** (12/12) | **−66.7 pp** |
| libri (OOD) | 75% (3/4) | 75% (3/4) | 0 |

来源：`table_split_ledger`。  
**先修 ASR/切句，不是先换 MT 模型。**

---

## 表④ A5 三路 ASR（只测不改）

### Pipeline 路径（同 VAD）

| mode | n | WER mean | ok_rate |
|------|---|----------|---------|
| online | 48 | 0.928 | 6.3% |
| offline | 48 | 0.915 | 8.3% |
| merged | 48 | 0.918 | 8.3% |

三路接近；VAD 漏检主导，看不出合并启发式的净收益。

### Oneshot（无 VAD，见表①下）

| mode | ami WER | 结论 |
|------|---------|------|
| online | 0.55 | 弱 |
| offline | **0.16** | 最强 |
| merged | 0.20 | 略伤 offline |

**不改 `prefer_2pass_asr`（任务硬约束）。** 数据提示：若日后允许改，应更偏向 offline。

---

## 表⑤ Phase B MT decode A/B

基线公开 harvest 重复率：`scan_repetition` → **8.0%**（4/50）。  
Ledger refs + harvest src 共 114 条：

| variant | repetition_rate | MT p50 | MT p95 | ≤120ms? |
|---------|-----------------|--------|--------|---------|
| greedy (旧默认) | 2.63% | 100ms | 213ms | ❌ |
| beam3 | 0% | 133ms | 345ms | ❌ |
| beam3+norepeat3 | 0% | 139ms | 268ms | ❌ |
| beam3+norepeat3+pen1.1 | 0% | 122ms | 252ms | ❌ |
| **greedy+nr3+pen1.1** | **0.88%** | **86ms** | 170ms | ❌（仍超，但最优折中） |

来源：`docs/mt_decode_ab_summary.json` + 附加 beam1 扫描。

**默认变更（有表依据）**：

| 项 | 旧 | 新 | 依据 |
|----|----|----|------|
| `MT_DECODE_BEAM` | 1 | **1**（不变） | beam3 P95 全面破门 |
| `MT_NO_REPEAT_NGRAM` | 0 | **3** | 重复 2.6%→0.9% |
| `MT_REPETITION_PENALTY` | 1.0 | **1.1** | 同上；p50 不升反降 |
| `VAD_MIN_UTT_SEC` | 0 | **0**（接线保留） | 缺连续音验收 |

说明：≤120ms 门是对照短通话句（~26ms）；本轮 AMI 长 ref 上 **连 greedy 也常破门**，不能因此开 beam。

---

## Phase C：`vala` 切点诊断

音频：`cache/harvest_public/ami_en2001b_1993_2020.wav`  
落盘：`docs/ami_cut_diagnosis.json`

本轮复跑 **未再现 `vala`**（切分与任务描述中的现场证据不同：强制收句 / ASR 复读）。观测：

- utt0 切点后 200ms `rms_post=0.005`（仍有能量）→ 存在偏早切可能  
- 功能词结尾率 10%（表②）

**结论**：半词切断在历史 harvest 中已证实；本轮该 clip 未稳定复现 `vala`。`VAD_MIN_UTT_SEC` 已实现于 `dual_track._finalize_utt`，**默认保持 0**。

### 2026-11-05 连续音 A/B（已做）

见 **`docs/VAD_MIN_UTT_AB.md`**：`0` vs `1.0` 在 ~46s AMI 连续+拼接上 **short_lt_1s 均为 0**（旋钮空转；切点多为 `forced_trunc`）。  
**仍不改默认。** 抽查笔记：`docs/ledger_review_15_notes.md`（ASR 锅成立）。未扩 postedit/TM，未改 `prefer_2pass`。

### 2026-11-05 空句分解 + 吞句修复 + TEN + 紧闸门

见 **`docs/EMPTY_UTT_DECOMPOSE_REPORT.md`**（头条分账以该文 Phase D 为准）：  
AMI pipeline **37.5%** vs MT-only **93.8%**；空 ASR 39→16；`VAD_BACKEND=ten`；吞句 bug 已修。

---

## 待人工复核 15 句

文件：`docs/ledger_review_15.jsonl`  
抽查笔记：`docs/ledger_review_15_notes.md`  
标签：wrong / wrong_empty_asr / borderline / mt_only_ok_vs_pipeline_wrong  
每条含 ref / src / tgt / mt_only / model-judge 理由。

---

## 代码与产物

| 路径 | 作用 |
|------|------|
| `scripts/extract_ami_parquet.py` | 本地 AMI parquet → 48 句 |
| `scripts/extract_fleurs_parquet.py` | 本地 FLEURS parquet → 12 句 |
| `scripts/build_ledger_corpus.py` | 合并 manifest |
| `scripts/eval_ledger.py` | 分账 + fragment + A5(pipeline) |
| `scripts/eval_asr_oneshot.py` | 无 VAD WER |
| `scripts/ab_mt_decode.py` | Phase B 四组 A/B |
| `scripts/ab_vad_min_utt.py` | Phase C 连续音 min-utt A/B |
| `scripts/diagnose_ami_cut.py` | Phase C |
| `scripts/scan_repetition.py` | 重复扫描 |
| `app/translate.py` / `app/config.py` | CT2 knobs |
| `app/dual_track.py` | `VAD_MIN_UTT_SEC` 接线 |

**未 commit**（按任务要求）。
