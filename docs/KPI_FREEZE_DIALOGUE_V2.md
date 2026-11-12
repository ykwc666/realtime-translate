# 对话 KPI 冻结说明 — `dialogue-kpi-v2-freeze`

冻结日期：2026-11-12
性质：**读数冻结，不是功能冻结。** 冻结的是"这一版在 MSLT 对话集上得多少分、怎么算分、多少算没变"，不是"不许再改产品"。

---

## 1. 冻结点

| 项 | 值 |
|---|---|
| commit | `275e432`（`fix(display): fall back when punctuation drops >=4 chars`） |
| pytest | **177 passed** |
| Test 语料 | `data/dialogue_corpus_v2/manifest.jsonl`，120 条（zh_en 60 + en_zh 60），120 个 wav / 24.8 MB，manifest sha256 前 16 位 `b63c3f25b988a420` |
| Dev 语料 | `data/dialogue_corpus_dev_v2/manifest.jsonl`，120 条（同上），120 个 wav / 25.6 MB，sha256 前 16 位 `788194fe1b75feb2` |
| 判定器 | `scripts/eval_dialogue_mslt.py` 当版（`asr_err`=去标点编辑距离；`mt_verdict` 严格 / `mt_verdict_sem` 并列，**只有严格判定进 KPI**） |

---

## 2. 四格 KPI（冻结值）

| 集 | 方向 | n | **asr_ok**（err≤0.35） | **mt_ok** | asr_err | given-ASR | empty | 碎片丢失 | 重复 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Test | zh_en | 60 | **51 / 60 = 85.0%** | **47 / 60 = 78.3%** | 0.2329 | 84.3%（43/51） | 0 | 0 | 1 |
| Test | en_zh | 60 | **47 / 60 = 78.3%** | **53 / 60 = 88.3%** | 0.2309 | 95.7%（45/47） | 0 | 3 | 0 |
| Dev | zh_en | 60 | **47 / 60 = 78.3%** | **47 / 60 = 78.3%** | 0.2294 | 85.1%（40/47） | 0 | 2 | 0 |
| Dev | en_zh | 60 | **46 / 60 = 76.7%** | **45 / 60 = 75.0%** | 0.2267 | 87.0%（40/46） | 1 | 8 | 0 |

en_zh 按参考句长度分桶（报 en_zh 时必须同时给 ≥9 词那一档，否则短题会把分数拉花）：

| 集 | ≤2 词 | 3–4 词 | 5–8 词 | ≥9 词 |
|---|---|---|---|---|
| Test | 11/15 | 6/8 | 9/10 | **27/27** |
| Dev | 11/16 | 5/8 | 8/12 | **22/24** |

---

## 3. 读数规则（比数字更重要）

重跑时对不上是常态，但**必须按这张表判，不许看到差 1 就喊回归**。

| 指标 | zh_en（两集） | en_zh（两集） | 说明 |
|---|---|---|---|
| `asr_ok` | **一条都不许差** | 允许 ±1 | 抖动源：2pass 投机快照依赖线程调度 |
| `mt_ok` | **一条都不许差** | 允许 ±1，但**必须指到具体 utt 的识别文本变了**（贴 old/new 两行） | 同上 |
| `asr_err_mean` | ±0.005 | ±0.005 | 单条 utt 的错字率抖动就够翻动这个量 |
| `punct_fallback` | Test 恒 1（`zh_en_027`），Dev 恒 0 | 0 | 变多说明标点模型行为变了，要查 |
| `punct_dropped` | Test 恒 1，Dev 恒 0 | 0 | 同上 |
| `arbiter_pick` | `{offline:71, online:1, merge:1}`（Test zh_en）| 见 detail | 只允许 1 条 merge（`002` partial 补全） |
| `empty_asr` | 0 | Test 0 / Dev 1 | Dev 那条是 `en_zh_047`（1 词参考、极轻） |

**超过容差就按回归处理**：先 diff `detail.jsonl` 找变的那条，再决定是回退还是改基线（改基线要 bump 版本名，见 §6）。

---

## 4. 红线（冻结期一律不动）

- `VAD_TEN_THRESHOLD = 0.4` / `VAD_SILENCE_SEC = 0.45` / `VAD_SPEECH_RMS_MIN = 0.035`
- `MIC_DENOISE = False`（降噪已 A/B 否决：AMI 27/48→20/48、WER 0.4433→0.4937、FLEURS 11/12→8/12）
- `ASR_2PASS_MERGE_MODE = "offline"`；`prefer_2pass_asr()` 不许动；不许调 online/offline 权重
- 不许扩 TM、不许加 postedit 规则、不许按失败句堆规则
- 不许改判定器阈值（`0.35` / `coverage 0.8` / `punct drop 4 字`）
- 不许为了让数字好看改任何实测值

---

## 5. 已知限制（写清楚，免得以后当成 bug 重查）

1. **zh_en 有 2 条真听错**：`Test zh_en 026`、`059`。两路 ASR 都错，oracle 也救不回 → 模型上限，不是策略问题。
2. **仲裁已经到顶**：单 utt 三选一（现 / 只 online / 只 offline / oracle）在四个集上分别是 41/35/42/42、43/16/43/45、37/37/37/38、43/14/43/44。**oracle 合计只多 3 条**，其中 1 条还是吞尾造成的。不要重开仲裁。
3. **en_zh 的失败集中在 ≤4 词碎片**：1 词参考（`dying` / `Ohh.` / `for`）ASR 只有 0 或 1，没有部分分。语料 wav 本身不坏（最短 0.53s），是题目没信息量。
4. **1 条噪声地板**：同一份音频、同一份代码跑两次，en_zh 会有 1 条 utt 的识别文本不同（`Dev en_zh 005` 实测 `going to … because` ↔ `gonna … 'cause`），`asr_err` / `coverage` / `mt_ok` 会跟着动 1 条。
5. **标点守卫阈值按字符数**：英文 ≤3 字母的短词被吃不会触发回退。目前无一例证据（`034` 查证是识别假设不同），所以不动阈值。
6. **`src_raw` 在假名映射路径不是 ASR 原文**：映射发生在赋值之前，`うん → Yeah.` 一旦命中，`src_raw` 存的是 "Yeah."。本版无此案例，分析时注意。

---

## 6. 变更纪律

- 冻结期内只许改"读数"相关的观测字段，改完必须重跑两集并把 §2 一起更新。
- 任何会让 §2 数字变化的改动（产品行为），必须 **bump 名字**（`dialogue-kpi-v3-…`）并在这里留一行"谁、何时、为什么"，不许在 v2 名义下悄悄改数。
- 每次重跑留档：`summary.json` 复制成 `summary_<变更名>.json` 再跑。

---

## 7. 复现

```bat
cd /d D:\xuanjian\realtime-translate
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_v2\manifest.jsonl --out-dir data\eval\dialogue_v2
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_dev_v2\manifest.jsonl --out-dir data\eval\dialogue_dev_v2
.\.venv\Scripts\python.exe -m pytest -q
```

对上了的判断标准：§2 四格里 `asr_ok` / `mt_ok` 全部落在 §3 容差内，`pytest 177 passed`。对不上先看 `detail.jsonl` 的 `src` / `src_raw` / `arbiter_pick` / `punct_fallback` 四个字段。

---

## 8. 本冻结**不**覆盖什么

- 真实麦克风、远场、房间混响
- 两人抢话 / 半句打断（机制在，未用真实语料验证）
- 口音、非母语发音
- 多人 / 会议室（远期目标，AMI 不是 KPI）
- TTS 音质与延迟体感（评测全程 `enable_tts=False`）

这些都要靠下一阶段的实麦冒烟，不能拿 MSLT 的数字代表。
