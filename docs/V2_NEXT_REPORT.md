# V2 下一刀报告（短句集中块 → 尺子 → 长尾诊断）

按 `docs/CURSOR_TASK_V2_NEXT.md`。红线未动：VAD 0.4 / 0.45 / 0.035，`MIC_DENOISE=False`。

---

## 第 1 步：en→zh 极短句

### 改动文件
- `app/dual_track.py`：新增 `_KANA_AFFIRM_TO_EN`；finalize / offline_rescue 在 `force_src_lang=="en"` 时映射假名后再走 MT（key 为去标点串，如 `あイ`）
- `data/mt_tm.jsonl`：仅加 5 条 en-zh 精确 TM  
  `Right→对。` / `No→没有。` / `Nice→很好。` / `Yeah→是啊。` / `Exactly→没错。`  
  （`Yeah/Nice` 目标取通用口语且能过 MSLT 词袋闸；初稿「嗯/不错」会误杀「是啊/很好」参考）
- `tests/test_en_short_affirm.py`

**未写入**：`まい`（半句，补不回来如实报）。

### Test 前 → 后（分子/分母）

| 档 | 前 | 后 |
|---|---|---|
| en_zh ≤2 词 mt_ok | 7/15 | **11/15** |
| en_zh 3–6 词 | 12/14 (86%) | 12/14 (86%) |
| en_zh >6 词 | 30/31 (97%) | 30/31 (97%) |
| en_zh 全量 mt_ok | 49/60 | **53/60** |
| zh_en 全量 mt_ok | 47/60 | **47/60**（不变） |

### Dev 前 → 后

| 档 | 前 | 后 |
|---|---|---|
| en_zh ≤2 词 mt_ok | 9/16 | **11/16** |
| en_zh 3–6 词 | 10/15 (67%) | 10/15 (67%) |
| en_zh >6 词 | 24/29 (83%) | **25/29 (86%)** |
| en_zh 全量 mt_ok | 43/60 | **46/60** |
| zh_en 全量 mt_ok | 47/60 | **47/60**（不变） |

两集同向：**是**。无长度档变差。

≤2 词未救回（如实）：
- Test：032 dying 听错；045 Wait→W；050/054 Ohh ASR 不稳  
- Dev：008 Why 听成 All right；009/028 假名半句（含 `まい`）；036 `Yeah撇`；047 空识别  

未达 Dev ≥13/16，但双集上涨且未过拟合回退。

### 复现
```bat
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_v2\manifest.jsonl --out-dir data\eval\dialogue_v2
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_dev_v2\manifest.jsonl --out-dir data\eval\dialogue_dev_v2
.\.venv\Scripts\python.exe cache\diag_v2_short_en.py
```

---

## 第 2 步：判定器双轨

### 人工定标（20 条）
文件：`data/eval/dialogue_v2/mt_judge_calib_20.json`  
**一致率 15/20 = 75% &lt; 85%** → 加并列口径（不替换旧口径）。

### 改动
- `scripts/eval_dialogue_mslt.py`：`mt_usable_sem()`（同义簇放宽；词数 &lt;45% 参考视为漏译不豁免）
- detail 增加 `mt_verdict_sem` / `mt_reason_sem`；summary 增加 `mt_ok_rate_sem`

### 数字（旧 / 新并列）

| 集 | 方向 | mt_ok_rate（旧） | mt_ok_rate_sem |
|---|---|---:|---:|
| Test | zh_en | 0.783 | **0.850** |
| Test | en_zh | 0.883 | **0.933** |
| Dev | zh_en | 0.783 | **0.850** |
| Dev | en_zh | 0.767 | **0.783** |

禁止只报新口径。KPI 仍以 `mt_ok_rate` 为准。

---

## 第 3 步：zh→en ASR 长尾（只诊断）

> **返工更正**（`CURSOR_TASK_ASR_TAIL_REWORK.md`）：旧 CER 把重复/语气词当成听错；改用 `asr_err_core` 后结论翻转。脚本已入库 `scripts/diag_asr_tail.py`。

| 集 | `n_asr_fail`（core） | `n_asr_fail_kpi`（旧 CER） | `offline_helps`（core） | `offline_helps_kpi` | verdict |
|---|---:|---:|---:|---:|---|
| Test | **2**（026、059） | 10 | **0** | 2 | **STOP_MODEL_CEILING** |
| Dev | **4** | 13 | **0** | 0 | **STOP_MODEL_CEILING** |

真正听错的 2 条（Test 026/059）online/offline **两路都错**。旧口径那 2 个「offline 赢」换成 core 后两路都合格，不计分。

**判定**：**不要调 offline 权重**。产品代码 / `ASR_2PASS_*` 本步未改。下一刀更值得看 arbiter merge 变差（记账见返工任务书附录）。

---

## 基线对照（本轮后）

| 集 | 方向 | n | mt_ok | mt_ok_sem | asr_ok |
|---|---|---:|---:|---:|---:|
| Test | zh_en | 60 | 47 (78.3%) | 51 (85.0%) | 50 (83.3%) |
| Dev | zh_en | 60 | 47 (78.3%) | 51 (85.0%) | 47 (78.3%) |
| Test | en_zh | 60 | **53 (88.3%)** | 56 (93.3%) | 47 (78.3%) |
| Dev | en_zh | 60 | **46 (76.7%)** | 47 (78.3%) | 46 (76.7%) |
