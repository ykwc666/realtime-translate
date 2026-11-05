# ASR 复读强切修复（`_asr_loop` / `has_repeated_span`）

日期：2026-11-05。任务：`docs/CURSOR_TASK_ASR_LOOP_CUT.md`。

**本次默认 = GUARD_MODE=legacy / KEEP_TAIL=False / REJECT_STALE=True / P90=False**

## 一句话结论

**23/48 不干净**：其中 **+1（008）归 REJECT_STALE**，**+1（046 awkward→ok）归 KEEP_TAIL 二次转写捞字**（不是切句救命）。  
去混淆后默认改为 **stale_only → AMI 22/48**；046 记为 ASR 漏字案例。  
机制证据保留：008 WER 大降；不要把总分当显著性。

---

## P0：`has_repeated_span` 误报表

改动：仅认**紧邻**重复；CJK 按字符 `min_span=4`；纯 ASCII 按词（≥3 词且拼接 ≥12 字符）或 ≥12 字符紧邻。

| 集合 | 修前 | 修后 |
|------|------|------|
| ledger AMI+FLEURS 60 条 ref（正确文本） | **24/60 = 40%** 误报 | **0/60** |
| 任务点名 FP 样例（Open the window… might/right） | True | **False** |
| 英文真复读 `you read through it you read through it again` | True | **True** |
| 中文真复读（时快时慢… / 延迟…延迟… 等） | True | **True** |

`ami_034` ref「WRITTEN TO A WRITTEN TO A」为 3 词口语自我修正；靠「拼接 ≥12 字符」挡掉，避免当 ASR 粘住。

单测：`tests/test_has_repeated_span.py`（18 条）+ `pytest -k "repeat or loop"` 全绿。

---

## P1 / P2 / 连带

| 项 | 配置默认 | 行为 |
|----|----------|------|
| P1 soft 守卫 | `ASR_LOOP_GUARD_MODE=soft`，`ASR_LOOP_STREAK=2` | 连续命中才 latch；强切前 `transcribe_offline_only` 复核，通过则取消 |
| P2 留尾 | `ASR_LOOP_KEEP_TAIL=True` | 复读强切后把末 0.8s 播入下一句 |
| 连带（修复读后暴露） | — | `_take_speculate`：`miss_grow` 改用 30ms 帧 **p90 RMS**；短稿 vs 更长 live partial → `miss_stale` |

不动：`VAD_*` 默认值、`ASR_2PASS_MERGE_MODE`、postedit/TM、模型。

---

## 闸门数字

### G0 — 纯函数

- 英文 ledger ref 误报：**0/60**
- 中文召回：参数化真复读 **100%**
- 点名 FP：`False`（命令见任务 §7）

### G1 — 单文件 `EN2002a_FEO072_008.wav`（`run.py --mode dualsim --no-tts`）

- 出现「强制收句（ASR 复读）」：**否**
- 输出词数：**≥16**（整句含 Open the window … straight away）
- 日志：`docs/asr_loop_g1_dualsim.log`

说明：ledger 评测带 **0.5s 前导静音** 时，旧路径会早投机；修后 `miss_stale` 取消短稿，src≈13 词（缺句首 Open the window），仍远好于修前 `Read through。`。

### G2 — AMI ledger 配对（48 条，对照 `ledger_eval_detail_before_loop_fix.jsonl`）

| 指标 | before | after | 闸门 |
|------|--------|-------|------|
| pipeline ok | 20/48 = 41.7% | **22/48 = 45.8%** | 目标 ≥26：**未过** |
| n_utts=0 | 16 | **16** | 不得增加：**过** |
| WER mean | 0.592 | **0.569** | — |
| McNemar | — | to_ok **+2** / from_ok **0** | +3 条仍在噪声内；本轮 +2 |

**原 asr_wer>0.45 且 n_utts>0 的 10 条：**

| id | before → after | wer | 备注 |
|----|----------------|-----|------|
| ami_008 | wrong → **ok** | 0.895→0.263 | 任务复现句 |
| ami_031 | wrong → **ok** | 0.652→0.304 | |
| ami_039 | wrong → wrong | 0.824→0.412 | 文本明显变长，仍未过 judge |
| ami_004/006/010/033/036/040/042 | wrong → wrong | 近似不变 | |

翻到 ok/awkward：**2/10**（目标 ≥5：**未过**）。

落盘：`docs/asr_loop_cut_detail.jsonl`，`docs/ledger_eval_summary_after_loop_fix.json`  
（**未覆盖** `ledger_eval_summary_after_offline_merge.json`）

### G3 — 合成 `cache/ab_weekday`

| wav | src（修后） | 相对 swallow 基线 |
|-----|-------------|-------------------|
| accept_phrases ×4 | 你好… / 今天… / 星期二 / 他现在… | 内容一致（你好后多一枚展示逗号） |
| tail_duosimo ×2 | 延迟… / 今天… | 一致 |
| pause_xingqi_er | **现在是星期三。** | 基线曾为「星期啊」——来自既有 **weekday 保护**（online 完整星期X），非本轮复读逻辑 |

无空句、无切碎；accept/tail **零内容回归**。

### G4 — pytest

`103 passed`（含本轮新增；全量 `tests/`）。

### G5 — 中文长句

合成 utt `audio_sec` 均 **≤4.8s**，无 >15s「该切不切」。

---

## 翻盘 / 掉盘 id

- **to ok：** `ami_008`, `ami_031`
- **from ok：** （无）

---

## 为何没到 26/48

1. **16 条空检**本轮明确不修 VAD。  
2. 其余失败多为短碎片 / 远场识别，不是复读误切。  
3. 复读误切修掉后，**早投机短稿**成为 ami_008 类主因；已补 `miss_grow` p90 + `miss_stale`，但句首仍可能被 VAD/气口吃掉。

---

## 去混淆（Q1–Q5 + 六臂）

### Q1 — ami_046 / b_only 第二句

`pipeline_src`：`So I I have you already calculated。 We calculated all this data .。What are you supposed to do that？`  
utt2 前 8 词：`We / calculated / all / this / data / . / What / are`  
与 ref `CALCULATED ALL THIS DATA` 重复：**calculated, all, this, data**。

### Q2 — pre vs b_only（脚本原文）

| flag | pre | b_only |
|------|-----|--------|
| GUARD_MODE | legacy | legacy |
| KEEP_TAIL | False | **True** |
| REJECT_STALE | False | **True** |
| MISS_GROW_P90 | False | **True** |

### Q3 — stale_only（legacy / TAIL=0 / STALE=1 / P90=0）

- pipeline_ok = **22/48**
- ami_046：n=2，awkward，src=`So I I have you already calculated。 Are you supposed to do that？`（**无** ALL THIS DATA）
- ami_008：长句 ok，`Read through it … straight away。`

### Q4 — tail_only（legacy / TAIL=1 / STALE=0 / P90=0）

- pipeline_ok = **21/48**
- ami_046：n=2，wrong，src=`…calculated。 We calculated list。`
- ami_008：仍 `Read through。` wrong

### Q5 — 当前默认（当时为 b_only 四开）连跑两次

- run1 ok=**23**，run2 ok=**23**；ami_046 两次文本一致（均含 We calculated all this data）。

### 查表

Q3=22、Q4=21 → 非「各 +1」纯加性；**STALE 单独 +1（008）**；**TAIL 单独不抬总分**；旧 23 多出的 046 ok 来自 TAIL 与 STALE/P90 捆在一起时的漏字补偿。  
按 P0-b：**关 TAIL**；046 记 ASR 漏字；默认 = **stale_only**。

FLEURS：b_only 与 stale_only 均为 **9/12**、WER 0.251。

条件式 TAIL 候选：`docs/ami_words_per_sec_stale_only.json`（P10 作候选，**未写入 config**）。

六臂落盘：`docs/ab_loop_vs_stale_6arm.json`（不覆盖原四臂 json）。

---

## 改动文件（本轮意图）

| 文件 | 内容 |
|------|------|
| `app/mt_policy.py` | `has_repeated_span` 紧邻 + 语种感知 |
| `app/dual_track.py` | soft 守卫、留尾、`miss_grow` p90、`miss_stale` |
| `app/config.py` | `ASR_LOOP_GUARD_*` / `ASR_LOOP_KEEP_TAIL` |
| `tests/test_has_repeated_span.py` | 新增 |

```
git diff --stat（相对工作区；dual_track 若含既有未提交改动会偏大）
 app/config.py                  |  +ASR_LOOP_* 
 app/mt_policy.py               |  has_repeated_span 重写
 app/dual_track.py              |  soft/留尾/spec miss
 tests/test_has_repeated_span.py|  新增
```
