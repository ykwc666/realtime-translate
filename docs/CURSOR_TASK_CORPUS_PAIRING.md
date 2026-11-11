# 任务书：重建对话 KPI 语料（修 T1/T2 配对）— 2026-11-11

> 给 Cursor。本轮**只修语料与测量，不改任何识别/翻译行为**。
> 红线：`VAD_TEN_THRESHOLD=0.4` / `VAD_SILENCE_SEC=0.45` / `VAD_SPEECH_RMS_MIN=0.035` / `MIC_DENOISE=False` 一个都不许动；`mt_usable()` 与 `asr_err<=0.35` 判定口径不许动。

---

## 0. 先看清楚问题（别急着改代码）

MSLT 每条有四个文件：

```
MSLT_Test_CH_1081.T0.ch.wav   音频
MSLT_Test_CH_1081.T1.ch.snt   逐字转写（含「呃」「重复」）
MSLT_Test_CH_1081.T2.ch.snt   顺滑版（当前被当作 src_ref）
MSLT_Test_CH_1081.T3.en.snt   T2 的英译（当前被当作 tgt_ref）
```

**音频对的是 T1，不是 T2。** 实例（`mslt_zh_en_022`，音频 2.29s，本地 raw 与 zip 时长一致，没有被重切）：

| 层 | 内容 |
|---|---|
| T1 逐字 | 更新换代会比较快。 |
| T2 现用 ref | 它叫火烧香蕉。 |
| T3 现用 tgt | It is called burning bananas. |
| 流式 Paraformer | 更新坏蛋会比较快 |
| 离线 SenseVoice | 更新坏代会比较快 |

两个独立模型都听成 T1。全量统计（字符相似度 <0.5 视为「两层不是同一句」）：

| 语料 | 条目 | T1 与 T2 对不上 | 比例 |
|---|---:|---:|---:|
| Test_EN | 2127 | 115 | 5% |
| Dev_EN | 2223 | 111 | 5% |
| Test_ZH | 1285 | 595 | **46%** |
| Dev_ZH | 1256 | 618 | **49%** |

英文侧符合文档（T2 只是 T1 去口癖），**中文侧近一半是另一句话**。这批条目上 CER 必然为 1.0，跟模型无关。

后果：Test 30 条里 11 条坏题 → `asr_ok` 上限恰好 19/30 = 63.3%（正好等于实测）；Dev 13 条坏题 → 实测 40%。**现在这两个数不是模型的分数，是坏题比例的分数。**

---

## 1. 改哪里

### 1.1 `scripts/import_mslt_dialogue.py`

新增配对校验，**先过滤再抽样**（顺序不能反）：

```python
def _norm_pair_text(s: str) -> str:
    # 去 MSLT 标注标签 → 去标点/空白 → 小写
    ...

def _pair_sim(t1: str, t2: str) -> float:
    # 空串返回 0.0；否则 difflib.SequenceMatcher(None, a, b).ratio()
    ...
```

规则：

1. 读 `T1` 与 `T2`，任一侧为空 → **丢弃**，记 `reason=empty_tier`。
2. `_pair_sim(T1, T2) < --min-pair-sim`（默认 **0.5**）→ **丢弃**，记 `reason=pair_mismatch`。
3. 通过校验的候选才进入抽样池；`random.Random(seed).shuffle()` 后再取 N。
4. 有效候选不足 N 时 **`exit 2`** 并打印 `valid=x need=y`，**不许**用坏条目补齐，也不许临时放宽阈值。

新增 CLI：

```
--min-pair-sim 0.5      # 固定默认 0.5，本轮禁止调
--n-zh-en 60 --n-en-zh 60
```

新增产物（与 manifest 同目录）：`rejected.jsonl`，每行 `{id, notes, reason, pair_sim, t1, t2}`，用于审计丢弃了哪些。

manifest 每条**新增三个字段**（只增不改）：

```json
{"pair_sim": 0.87, "src_verbatim": "<T1 原文>", "src_tier": "T2"}
```

`src_ref` 仍然是 **T2**，`tgt_ref` 仍然是 **T3**。

> **两个禁止的做法**（会引入新的自欺）：
> - 不许把 `src_ref` 换成 T1：T3 是 T2 的译文，换 T1 会让 src/tgt 不再对应。
> - 不许用机器翻译把 T1 补成英文参考：那是拿 MT 当考卷、再拿同一个 MT 去考。

### 1.2 输出目录（不许覆盖现有语料）

```
data/dialogue_corpus_v2/        # Test
data/dialogue_corpus_dev_v2/    # Dev
```

`data/dialogue_corpus/` 与 `data/dialogue_corpus_dev/` **保留原样**（旧基线要能复查，虽然它已作废）。
`.gitignore` 按现有 `data/dialogue_corpus/raw/` 的写法补两行 raw 目录；**manifest.jsonl 与 rejected.jsonl 要提交**（小文件，审计用）。

### 1.3 `scripts/eval_dialogue_mslt.py`

只做两件事：

1. 从 manifest 读 `pair_sim`，在 `summary.json` 增加 `pair_sim_min` / `pair_sim_p10` / `pair_sim_p50`（证明这一版没有坏题混进来）。
2. `detail.jsonl` 增加 `pair_sim` 字段透传。

**不改**任何判定函数、阈值、方向逻辑。`--manifest` / `--out-dir` 保持现有行为。

### 1.4 先提交现有未提交改动

HEAD（`119b526`）里**没有** `asr_online/asr_offline`、**没有** `fragment_lost`、import 也**没有** `--out/--force` 保护——这些全在工作区。不先提交，下面所有数字都无法从 commit 复现。

先提交：`app/asr_stream.py`、`app/dual_track.py`、`scripts/eval_dialogue_mslt.py`、`scripts/import_mslt_dialogue.py`、`.gitignore`（与本次改动分开两个 commit，先提交观测字段那批）。

---

## 2. 跑什么

```bat
:: 1) 重建 Test 集（60/60，滤完仍要抽满）
.\.venv\Scripts\python.exe scripts\import_mslt_dialogue.py --zip data\dialogue_corpus\MSLT_Corpus.zip --out data\dialogue_corpus_v2 --prefer-split Test --seed 11 --n-zh-en 60 --n-en-zh 60

:: 2) 重建 Dev holdout
.\.venv\Scripts\python.exe scripts\import_mslt_dialogue.py --zip data\dialogue_corpus\MSLT_Corpus.zip --out data\dialogue_corpus_dev_v2 --prefer-split Dev --seed 11 --n-zh-en 60 --n-en-zh 60

:: 3) 各测一遍（代码与阈值一律不许动）
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_v2\manifest.jsonl --out-dir data\eval\dialogue_v2
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_dev_v2\manifest.jsonl --out-dir data\eval\dialogue_dev_v2

:: 4) 单测
.\.venv\Scripts\python.exe -m pytest -q
```

---

## 3. 交什么

新建 `docs/DIALOGUE_BASELINE_MSLT_V2.md`（**不要覆盖** `DIALOGUE_BASELINE_MSLT.md`，旧的保留并在开头标注「作废：T1/T2 未校验」）：

1. **过滤账**：Test/Dev 各方向 `候选数 / 丢弃数（empty_tier、pair_mismatch）/ 保留数 / 实抽数`。
2. **新基线表**：zh_en、en_zh 各 `n / asr_ok / mt_ok / mt_ok_given_asr(n) / empty_asr / fragment_lost / fragment_dup / pair_sim_p10`。
3. **新旧对照**：同一脚本、同一判定器下，旧 Test（70% / 63.3%）与新 Test 并排放，一句话说明差异来自语料而非代码。
4. **holdout 结论**：新 Test 与新 Dev 的 `mt_ok` 差多少。这是本次唯一要回答的问题——去掉坏题后，Test 与 Dev 还差多少。
5. **复现命令**：上面 4 条原样贴出，加本机 Python 版本。

---

## 4. 验收标准（我会按这个复核）

| # | 项 | 通过条件 |
|---|---|---|
| A | 配对纯净度 | 新 manifest 里 `min(pair_sim) >= 0.5`，且 0 条 `src_ref` 为空 |
| B | 样本量 | zh_en、en_zh 各 **≥ 50** 条（Test 与 Dev 都算） |
| C | 可复现 | 同命令、同 seed 重跑，manifest 的 id 列表完全一致 |
| D | 行为未变 | `git diff` 不含 `app/` 下任何文件；VAD 三阈值与 `MIC_DENOISE` 未变 |
| E | 单测 | `pytest -q` 全绿 |
| F | 报告齐全 | 上面第 3 节 5 项全有，且数字与 `summary.json` 逐项对得上 |

**不算通过的情形**（直接退回）：

- 为了凑够 60 条把 `--min-pair-sim` 往下调。
- 用 T1 替换 `src_ref`，或用 MT 补英文参考。
- 顺手「优化」了 postedit / TM 条目 / `prefer_2pass` / `ASR_2PASS_*`。
- 只报新数字、不报过滤账（那样无法判断是不是又混进了坏题）。

---

## 5. 本轮明确不做

- 不碰 VAD、不碰 `MIC_DENOISE`、不碰 `ASR_LOOP_*` / `ASR_SPEC_*` / `prefer_2pass`。
- 不加 TM 条目、不加 postedit 规则、不调 beam。
- 不改 `mt_usable` 的 0.35 阈值，不改「嗯/对是否同桶」的口径（要改另立提案）。
- 不动切句与会后时间戳。

本轮唯一目标：**让 KPI 集第一次真的是这套产品在被打分。**
