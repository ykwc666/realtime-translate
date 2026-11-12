# 任务书：标点吞尾回退守卫（PUNCT_GUARD）

仓库：`D:\xuanjian\realtime-translate`
基线：`c6e6690`（173 passed，OBS_FIX 已完成）
本轮性质：**只改展示层**。ASR / MT / TTS / 标点模型一律不动。

---

## 0. 一句话

标点模型偶尔会把最后半句吃掉（全量 240 条 utt 里只有 1 条）。这一轮加一道守卫：**发现吃掉 ≥4 字，就把展示文本退回 ASR 原文**，不许改标点模型、不许调阈值凑数。

---

## 1. 证据：规模只有 1 条，但它是产品可见缺陷

我用 `data/eval/dialogue_v2/detail.jsonl` + `dialogue_dev_v2/detail.jsonl` 逐 utt 比了 `src`（展示文本）和 `src_raw`（标点模型之前）：

```
Test 展示层字符差（正数=展示比原文短）：{0 字: 138 条, -7 字: 1 条}
Dev  展示层字符差：                    {0 字: 140 条}
```

**240 条里 239 条一个字都没丢。** 所以这不是"标点模型普遍吃字"，是个例，不要为它换模型。

唯一那条例外是 `mslt_zh_en_027`：

```
ASR 原文 : 些葱，然后把这个嗯土豆丝儿放下去，要不断的翻炒。呃，放下去以后呢
展示文本 : 些葱，然后把这个嗯土豆丝儿放下去。要不断的翻炒。
MT 吃的  : 些葱，然后把这个嗯土豆丝儿放下去，要不断的翻炒。呃，放下去以后呢   ← 完整
MT 译文  : "Some onions, ... Uh, put it down later."                        ← 完整
```

**关键点：MT 和 TTS 拿到的是完整句子，只有字幕少了最后半句。** 用户会看到"译文里说了放下去以后"，字幕却没有——这是展示层缺陷，不是识别缺陷。

副作用（要一起修掉的）：这条 `asr_hyp` 用的是被吃过的文本，所以 CER 被算成 **0.5217**；用原文只有 **0.3043**。修完这条的 `asr_ok` 应该由 fail 变 ok。

---

## 2. 明确不做：不许换标点策略

上一轮建议里写了"修展示层**或换标点策略**"。**换标点策略这一支我否掉**：为 1/240 的个例换一个模型，等于拿全量基线做赌注，还得重测所有档位。只做守卫。

同样不许做的事：

- 不许把 `dedupe_display_punct()` 改掉（它正常只处理重复标点，实测 239 条差 0 字）
- 不许把 `ASR_PUNCT_DISPLAY` 改成 False（那等于关掉标点，字幕全变没标点）
- 不许改阈值 4 字（见 §3）

---

## 3. 改法

### 3.1 位置

`app/dual_track.py:1356-1372`，当前长这样：

```python
display_src = full
if bool(getattr(config, "ASR_PUNCT_DISPLAY", False)):
    from .punct import add_punctuation

    puncted, punct_ms = add_punctuation(full)
    display_src = dedupe_display_punct(puncted or full)
    if punct_ms > 0 and display_src != full:
        print(f"[punct {punct_ms:.0f}ms] {full!r} → {display_src!r}")
    _n_raw = re.sub(r"[^\w\u4e00-\u9fff]+", "", full, flags=re.UNICODE)
    _n_disp = re.sub(
        r"[^\w\u4e00-\u9fff]+", "", display_src, flags=re.UNICODE
    )
    _drop_n = len(_n_raw) - len(_n_disp)
    if _drop_n >= 4:
        print(f"[punct drop {_drop_n}字] {full!r} → {display_src!r}")
        self.stats.punct_dropped = True
```

### 3.2 要改成

```python
    _drop_n = len(_n_raw) - len(_n_disp)
    if _drop_n >= 4:
        print(f"[punct drop {_drop_n}字] {full!r} → {display_src!r}")
        self.stats.punct_dropped = True
        # 内容被吃：展示退回 ASR 原文（MT 早已用 full，不受影响）
        display_src = full
        self.stats.punct_fallback = True
        print(f"[punct guard fallback {_drop_n}字] → {display_src!r}")
```

注意顺序：`punct_dropped` 记的是"检出丢失"，`punct_fallback` 记的是"我们回退了"。两个字段都要，不要合成一个。

### 3.3 新增观测字段

`app/dual_track.py:122` 附近（`src_raw` / `punct_dropped` 旁边）：

```python
    punct_fallback: bool = False
```

`scripts/eval_dialogue_mslt.py` 的 `_utt_dict()` 里加：

```python
        "punct_fallback": bool(getattr(finished, "punct_fallback", False)),
```

**不要动 `asr_hyp` / `asr_err` 这两列的定义**，它们会自然跟着 `src` 变化。

### 3.4 想清楚一件事再动手

回退之后 `display_src = full`，而 `full` 也有流式模型自己的标点（027 的原文里就有「，」「。」），只是没有句末"。"。字幕会变成：

```
些葱，然后把这个嗯土豆丝儿放下去，要不断的翻炒。呃，放下去以后呢
```

**接受这个结果**。少一个句号，比少半句话好。

---

## 4. 测试要求

不要把逻辑写死在 `if` 里就完事，抽成可测的纯函数（不加载模型）：

```python
    @staticmethod
    def display_drop_chars(full: str, display: str) -> int:
        """展示文本相对 ASR 原文少几个字（去标点后按字符数）。"""

    @staticmethod
    def pick_display_src(
        full: str, puncted_display: str, min_drop: int = 4
    ) -> tuple[str, int]:
        """返回 (最终展示文本, 丢失字数)；丢失 >= min_drop 时回退 full。"""
```

`tests/test_punct_guard.py` 至少 4 条：

1. 完全不丢（加句号）→ 用标点结果，drop=0
2. 丢 3 字 → **不回退**（阈值边界）
3. 丢 7 字（照抄 027 的真实文本）→ 回退原文，drop=7
4. 标点结果为空 → 回退原文

---

## 5. 重跑与报数

先把当前结果留档，再重跑：

```bat
copy data\eval\dialogue_v2\summary.json data\eval\dialogue_v2\summary_pre_guard.json
copy data\eval\dialogue_dev_v2\summary.json data\eval\dialogue_dev_v2\summary_pre_guard.json
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_v2\manifest.jsonl --out-dir data\eval\dialogue_v2
.\.venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_dev_v2\manifest.jsonl --out-dir data\eval\dialogue_dev_v2
.\.venv\Scripts\python.exe -m pytest -q
```

**跑之前先记住噪声地板**：`c6e6690` 只改观测字段的那一轮，en_zh 仍出现 1 条抖动（`asr_err` 0.2309→0.2323，`coverage` 0.9684→0.9517）。所以 en_zh 允许 ±1 条差，**zh_en 一条都不许差**（除了 027）。

期望（我的算法，你可以自己核）：

| 项 | 现在 | 修完 |
|---|---|---|
| Test zh_en `asr_err_mean` | 0.2365 | **≈0.2329**（只 027 从 0.5217→0.3043） |
| Test zh_en `asr_ok_le_0.35` | 50 | **51** |
| Test zh_en `mt_ok` | 47 | **47（不许变）** |
| Dev zh_en | — | **完全不变**（该方向没有吞尾条目） |
| en_zh 两集 / Dev en_zh | — | 允许 ±1 条（噪声），`mt_ok` 不许变 |

---

## 6. 红线（一条都不许松）

- `VAD_TEN_THRESHOLD = 0.4` / `VAD_SILENCE_SEC = 0.45` / `VAD_SPEECH_RMS_MIN = 0.035`
- `MIC_DENOISE = False`
- `ASR_2PASS_MERGE_MODE = "offline"`；`prefer_2pass_asr()` 一行不许动
- 不许换 ASR 模型、不许换标点模型、不许改 `add_punctuation` 的调用参数
- 不许扩 TM、不许加 postedit 规则
- 不许改 4 字阈值、不许为了凑分布改 `prefer_final_asr()`
- 不许为了好看改任何实测数字

---

## 7. 验收（A–H，全过才算完成）

| # | 检查 | 方法 | 期望 |
|---|---|---|---|
| A | pytest | `python -m pytest -q` | 全绿，总数 ≥ 174 |
| B | 变化面只有 027 | 对比 `summary.json` vs `summary_pre_guard.json` | Test zh_en `asr_err_mean` 0.2365→0.2329、`asr_ok` 50→51；**其余三项主 KPI 不变**；Dev zh_en 一字不变，en_zh 两集允许 ±1 条噪声 |
| C | `mt_ok` 四集不变 | 同上 | 47 / 47 / 53 / 46（Test zh_en / Dev zh_en / Test en_zh / Dev en_zh） |
| D | 展示文本不再变短 | 逐 utt 比 `len(norm(src))` 与 `len(norm(src_raw))` | **不存在** `src` 比 `src_raw` 短的 utt；027 差 0；002 差 +1（partial 补全，正常） |
| E | 回退计数 | `detail.jsonl` | `punct_fallback=true` **恰好 1 条**（Test 027）；`punct_dropped=true` 仍 1 条 |
| F | pick 分布 | `utts[].arbiter_pick` | Test zh_en `{offline:71, online:1, merge:1}`；027 由 merge→offline；两路不同的 210 条里 **210 对 0 错 0 例外** |
| G | 日志 | 搜 eval 日志（注意是 UTF-16LE，先转码） | `[punct guard fallback` 恰好 1 次；`[punct drop` 仍 1 次 |
| H | 没偷改产品 | `git diff --numstat c6e6690` | `app/asr_stream.py` 0 行、`app/config.py` 0 行；`dual_track.py` 只改展示层这段 + stats 字段 |

### 报数格式

```
A pytest:        ___ passed
B KPI 变化:      Test zh_en asr_err_mean ___ / asr_ok ___；其余集 ___
C mt_ok:         Test zh_en __ / Dev zh_en __ / Test en_zh __ / Dev en_zh __
D 展示变短:      ___ 条（必须 0）
E punct_fallback:___ 条（期望 1）；punct_dropped ___ 条
F pick 分布:     Test zh_en ___；210 条里对 __ / 错 __ / 例外 __
G 日志:          guard ___ 次，drop ___ 次
H git diff:      asr_stream ___ 行，config ___ 行
```

另附：027 修改前后的 `src` 原文对照（一行 old、一行 new）。

---

## 8. 如果你认为我错了

按这个顺序回，别直接开写：

1. 先贴你自己数的展示层字符差分布（`src` vs `src_raw`，去标点，全部 279 条 utt）。如果不是"239 条 0 字 + 1 条 -7 字"，第 1 节作废，请贴统计脚本。
2. 再贴 027 的 `tgt`（MT 译文）——如果里面没有 "put it down later"，说明 MT 也没拿到那半句，那 §1 的"MT 是完整的"就错了，整份作废。
3. 最后确认 `punct_fallback` 会不会被 `_stamp_asr_obs` 的第二次覆盖影响（`display_src` 在 `:1383` 又被用了一次）。如果回退后显示文本被别处改写，先报出来。

三条都推翻不了，就按第 3 节做。做完只交第 7 节的表，不要额外发挥。
