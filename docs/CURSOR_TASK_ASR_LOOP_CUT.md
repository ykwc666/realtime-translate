# 任务：修 ASR 复读强切（`_asr_loop`）

> 目标：把 AMI pipeline ok 从 **20/48** 抬到 **≥26/48**，不动 VAD、不动 MT、不改 2pass 默认。
> 工作区：`D:\xuanjian\realtime-translate`（Windows，CMD；虚拟环境 `.venv\Scripts\python.exe`）。

---

## 1. 已核实的事实（不用重查，直接采信）

**(a) 失败结构**（`docs/ledger_eval_detail.jsonl`，AMI 48 条）

| 桶 | 条数 |
|---|---|
| `n_utts=0` 空句 | 16 |
| `asr_wer>0.45` 有字但错 | 10 |
| `thin coverage` 边缘 | 2 |
| ok | 20 |

`n_utts` 分布：`1→30, 0→16, 2→1, 3→1`。**46/48 条只出过 0 或 1 句**，没有"下一句"可并入。

**(b) 10 条 `asr_wer` 的形态**：其中 8 条 `src` 的词全部出现在 `ref` 里（子集），长度只有 ref 的 **11%~69%**（中位 ~28%）。
不是"半句差一点"，是**丢了 65~90% 的身子**。

**(c) 复现实验（同一文件、同一 `VAD_BACKEND=ten`、同一引擎，只改评测脚本的 0.5s 前导静音）**

```
data\ledger_corpus\ami\EN2002a_FEO072_008.wav   （5.14s，ref 19 词）

pad=0.0s → audio=4.46s  src='Open the window. You read through it. You might click on, you know, close it…'
pad=0.5s → audio=3.20s  src='Read through。'      ← ledger 记录的就是这一条
```

pad=0.5s 的日志：

```
--- ASR 复读检测，准备强切 ---
--- 强制收句（ASR 复读）---
[ASR final ... samples=6400 ...] Read through
```

**(d) 根因已定位到纯函数**：`app/mt_policy.py:272` 的 `has_repeated_span(text, min_span=4)` 对**正确英文文本的误报率是 40%**：

```
AMI   48 条 ref（全部正确）→ 18 条被判"复读"   37.5%
FLEURS 12 条 ref（全部正确）→  6 条被判"复读"   50.0%
合计   60 条 → 24 条                            40.0%
```

误报样本：`ami_002 006 008 012 021 023 024 026 027 031 033 034 035 037 039 042 043 046`

而 10 条 `asr_wer` 失败里，`006 008 031 033 039 042` **正好都在误报名单上**（6/10）。

单条示例：

```python
has_repeated_span('Open the window, You read through it. You might click on, you know, close it again straight away')
# True   ← "might" 和 "right" 里都有 "ight"
has_repeated_span('Yeah, just to give them an idea, general idea what there is and so they can pick a')
# True   ← "idea" 出现两次
```

该函数按**中文**设计（去空格、4 字重复、允许出现在任意位置）。英文单词短、字母组合重复率高，4 字符重复几乎是必然事件。**所以这不是"偶发误判"，是系统性误判。**

**(e) 触发链**（`app/dual_track.py`）

```python
744:  if has_repeated_span(text):     # 任意一次 partial 命中
745:      self._asr_loop = True       # 直接置 latch，无去抖、无复位
...
1148: if freeze or too_long or self._asr_loop:
         → 立刻强切
         → self.endpoint.reset()
         → _finalize_utt(当前 buf)   # 只结算到强切点，之后的音频丢弃
```

命中后不会重开新句，所以 `n_utts` 停在 1，余下 1~4 秒语音**永久丢失**。

---

## 2. 要你做的事（按顺序，P0 不过不许做 P1）

### P0　把判据改对（纯函数，先跑单测，不跑模型）

改 `app/mt_policy.py::has_repeated_span`，两个约束同时加：

1. **只认紧邻重复**，不认"任意位置出现"。
   真复读是 `...you read through it you read through it`，是**背靠背**的。
   现在 `t.find(span, i+n) >= 0` 允许中间隔任意距离，这才是误报来源。
2. **语言感知的 `min_span`**：
   - 含 CJK：按字符，`min_span=4`（保持现状）
   - 纯 ASCII：按**词**，连续 ≥3 词重复，或 ≥12 个字符紧邻重复

签名保持向后兼容：`has_repeated_span(text, min_span=4)`，新增内部判定，不要改调用方。

**P0 闸门（必须先给出这张表才能往下走）**

```cmd
.venv\Scripts\python.exe -m pytest tests\ -q -k "repeat or loop"
```

| 集合 | 要求 |
|---|---|
| 60 条 ledger `ref` 全文（AMI 48 + FLEURS 12，全部正确） | 误报 **0** 条（现状 **24 条**，见 1(d)） |
| 20 条合成中文正样本（`cache\ab_weekday\*` 的 ASR 输出） | 误报 ≤1 条 |
| 现有中文复读样本（`tests` 里已有的用例） | 召回 **100%**，一条都不许漏 |
| 英文真复读（新增用例） | `"you read through it you read through it again"` 必须判 **True** |

如果 `tests` 里没有英文复读用例，**新增一个**：`"...you read through it you read through it again"` 必须判 True。

### P1　命中后不许破坏性切句（防御纵深）

即使 P0 之后仍有真复读，也不能"一命中就砍”。

改 `app/dual_track.py`：

```python
# app/config.py 新增
ASR_LOOP_GUARD_MODE = "soft"    # legacy | soft；默认 soft，可一键回滚
ASR_LOOP_STREAK     = 2         # 连续命中次数才置位（现状 1 次）

# _run_partial 内
if has_repeated_span(text):
    self._loop_hits += 1
else:
    self._loop_hits = 0
if ASR_LOOP_GUARD_MODE == "soft":
    if self._loop_hits >= ASR_LOOP_STREAK:
        self._asr_loop = True          # 只置标记，不立刻切
else:
    if has_repeated_span(text):        # legacy 行为，原样保留
        self._asr_loop = True
```

`soft` 模式下，`1148` 行的分支改为：

1. 置位后**不切句**，先对该句做一次整段离线重解（`transcribe_offline_only(self.buf.asarray())`）；
2. 重解结果**仍然** `has_repeated_span` → 才允许强切；
3. 重解结果正常 → 清标记（`_asr_loop=False; _loop_hits=0`），继续攒句。

理由：`ASR_2PASS_MERGE_MODE="offline"` 已经是以离线结果为准，流式 partial 的复读不应该有一票否决权。

### P2　万一必须强切，余音不许丢

强切分支里，`_finalize_utt` 之后：

```python
tail = self.buf.asarray()[cut_at:]      # cut_at 取不到就退化为"最后 0.8s"
self._finalize_utt(seg)
if tail.size >= int(0.35 * config.SAMPLE_RATE):
    self._begin_utt()
    self.buf.append(tail)
    self.endpoint.reopen_after_hold()
```

`P2` 只在前两步仍无法覆盖时启用；启用与否由 `ASR_LOOP_KEEP_TAIL=True/False` 控制，默认 `True`。

---

## 3. 验收闸门（全部要给数字，不许只写"已通过"）

| # | 闸门 | 判据 |
|---|---|---|
| G0 | P0 纯函数表 | 英文误报 0 / 中文召回 100% |
| G1 | 单文件回归 | `run.py --mode dualsim --file data\ledger_corpus\ami\EN2002a_FEO072_008.wav --no-tts` 输出 ≥16 词，且**不再出现**「强制收句（ASR 复读）」 |
| G2 | ledger 配对 | 10 条 `asr_wer` 中 **≥5 条**转 ok/awkward；16 条 `n_utts=0` **不得增加**；总数 **20/48 → ≥26/48** |
| G3 | 合成集 | `cache\ab_weekday\*` 四句文本零回归（逐字对比） |
| G4 | 单测 | `pytest tests\ -q` 全绿（当前基线 84 collected） |
| G5 | 中文 | 任一中文复读/长句样本不得出现"该切不切"（句长 > 15s 视为失败） |

**G2 必须做配对统计**（同一批 48 条，McNemar），不要只报绝对值：`n=48` 时 95%CI ≈ ±14pp，41.7%→47.9%（+3 条）在噪声内，**不算通过**。

---

## 4. 回滚条件

任一成立即回滚到 `ASR_LOOP_GUARD_MODE="legacy"`，并把数据留在报告里：

- G2 `n_utts=0` 增加 ≥1 条
- G3 任一句文本回归
- G4 出现新的失败用例
- G5 出现 >15s 的长句（说明该切不切）

回滚必须是**改配置一行**，不是 revert 代码。

---

## 5. 明确不要做

- 不动 `VAD_SPEECH_RMS_MIN`、`VAD_WEAK_GAIN_MAX`、`VAD_MIN_UTT_SEC`、`VAD_FORCE_END_UNCHANGED*`
- 不改 `ASR_2PASS_MERGE_MODE`
- 不加任何词表白名单（包括 weekday）
- 不改 `mt_postedit` / TM
- 不换 ASR 模型、不装新依赖
- 不做"短尾并入下一句"——46/48 条 `n_utts≤1`，没有下一句

---

## 6. 交付物

1. `docs/ASR_LOOP_CUT_AB.md`：P0 误报表 + G1~G5 逐项数字 + 配对前后对照（哪几条翻了、哪几条掉了，列 id）
2. `docs/asr_loop_cut_detail.jsonl`：修后重跑的 ledger 明细
3. `docs/ledger_eval_summary_after_loop_fix.json`：修后汇总（**不要覆盖** `ledger_eval_summary_after_offline_merge.json`）
4. `git diff --stat`，说明改了哪几个文件、每个文件多少行
5. 一句话结论：这轮值不值得保留

---

## 7. 复现命令（CMD）

```cmd
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe -c "from app.mt_policy import has_repeated_span as f; print(f('Open the window, You read through it. You might click on, you know, close it again straight away'))"
.venv\Scripts\python.exe run.py --mode dualsim --file data\ledger_corpus\ami\EN2002a_FEO072_008.wav --no-tts
.venv\Scripts\python.exe scripts\eval_ledger.py --corpus ami --skip-a5
```

第一条现在输出 `True`——这就是要修的东西。修完必须是 `False`。

批量统计误报率（P0 完成后重跑，应为 0/60）：

```cmd
.venv\Scripts\python.exe -c "import json;from app.mt_policy import has_repeated_span as f;rows=[json.loads(l) for l in open('docs/ledger_eval_detail.jsonl',encoding='utf-8')];print(sum(1 for r in rows if f(r['ref'])),'/',len(rows))"
```
