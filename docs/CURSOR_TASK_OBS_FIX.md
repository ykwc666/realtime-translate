# 任务书：撤回「仲裁」方向，改修观测字段（OBS_FIX）

仓库：`D:\xuanjian\realtime-translate`
基线：`5a73f0a`（168 passed）
本轮性质：**只改观测，不改产品行为**。KPI 一个数字都不许变。

---

## 0. 一句话

上一轮我提出"仲裁合并 3/120 条变差，值得开一刀"，**这个判断是错的，本任务书撤回它。**

错的原因不是数据抄错了，是我被一个字段名骗了：`arbiter_pick`。它写着 "merge"，但这个项目从来没有做过合并。这个字段同时掩盖了三件事：没有合并、字段本身双向误标、评测取的是展示文本而不是识别文本。

本轮只修后两件——第一件不是 bug，是设计，而且实测证明它是对的（见第 4 节）。

---

## 1. 证据一：这个项目根本没有"合并"

`app/config.py:92`

```python
ASR_2PASS_MERGE_MODE = "offline"
```

`app/asr_stream.py:83-88`

```python
merge_mode = (mode or getattr(config, "ASR_2PASS_MERGE_MODE", "legacy") or "legacy")
merge_mode = str(merge_mode).strip().lower()
if merge_mode == "offline":
    return o
if merge_mode == "online":
    return s
```

`offline` 模式下，`prefer_2pass_asr()` 在排除了「offline 为空」和「星期保护」之后，**直接返回 offline，一路到底**。它不会拼接、不会取优、不会做任何融合。函数名 `prefer_2pass` 的 "prefer" 是"偏好"，不是"合并"。

所以我上一轮说的「merge 不是取优」，真实情况是：**没有 merge 这个动作，只有选一路。**

---

## 2. 证据二：`arbiter_pick` 在最需要它的时候 98% 是错的

### 2.1 现在报出来的分布

用 `utts[]` 数组逐 utt 统计（不是顶层字段）：

| 集 | 方向 | utt 数 | pick=merge | pick=online | pick=offline |
|---|---|---:|---:|---:|---:|
| Test | zh_en | 73 | 65 | 8 | 0 |
| Test | en_zh | 66 | 63 | 1 | 2 |
| Dev | zh_en | 75 | 62 | 12 | 1 |
| Dev | en_zh | 65 | 65 | 0 | 0 |
| **合计** | | **279** | **255** | **21** | **3** |

一个"永远选 offline"的策略，却报出 91% 的 merge。这不自洽。

### 2.2 先分清：不是每条都需要 pick

两路 ASR 输出一样时，选了谁无所谓，标什么都无害。所以按「两路是否一致」切开：

| 集 | 方向 | utt | 两路一致（pick 无意义） | 两路不同（pick 必须准） |
|---|---:|---:|---:|---:|
| Test | zh_en | 73 | 27（37%） | 46 |
| Test | en_zh | 66 | 3（5%） | 63 |
| Dev | zh_en | 75 | 31（41%） | 44 |
| Dev | en_zh | 65 | 8（12%） | 57 |
| **合计** | | **279** | **69（25%）** | **210** |

顺带一个观察：en_zh 只有 5% / 12% 一致，英文侧两路分歧比中文侧大得多（中文 37% / 41%）。

### 2.3 这 210 条里，pick 只对了 4 条

| 集 | 方向 | 两路不同 | pick 对 | pick 错 |
|---|---:|---:|---:|---:|
| Test | zh_en | 46 | **0** | 46 |
| Test | en_zh | 63 | 3 | 60 |
| Dev | zh_en | 44 | **1** | 43 |
| Dev | en_zh | 57 | **0** | 57 |
| **合计** | | **210** | **4（1.9%）** | **206** |

**在最需要它的 210 条上，这个字段的正确率是 1.9%。** 它现在唯一还能用的地方，是那 69 条"两路一致"——而那些条根本不需要它。

### 2.4 真值：几乎全是 offline

| 集 | 方向 | 两路不同 | 真实=offline | 真实=online | 两者都不是 |
|---|---:|---:|---:|---:|---:|
| Test | zh_en | 46 | 43 | 1 | 2 |
| Test | en_zh | 63 | 61 | 1 | 1 |
| Dev | zh_en | 44 | 44 | 0 | 0 |
| Dev | en_zh | 57 | 57 | 0 | 0 |
| **合计** | | **210** | **205** | **2** | **3** |

加上两路一致的 69 条（它们也全部等于 offline），**Dev 两个方向共 140 条 utt，无一条例外**。这才符合 `ASR_2PASS_MERGE_MODE = "offline"`。

### 2.5 那 5 条例外

| 方向 | id | 现报 pick | 真实 | src | online | offline | 原因 |
|---|---|---|---:|---|---|---|---|
| zh_en | 008 | merge | **online** | `你你你有。` | `你你你有` | `你你你。` | offline 丢了"有"，online 对 |
| en_zh | 032 | online | **online** | `三嗯。` | `三嗯` | `.` | offline 只剩一个句号 |
| zh_en | 002 | online | 两者都不是 | `也是这么认同的。` | `也是这么认同` | `也是这么认同。` | **partial 补全**：`prefer_final_asr` 发现流式 partial 是 final 的前缀且更长，取了 partial |
| zh_en | 027 | merge | 两者都不是 | `…要不断的翻炒。` | `…放下去以后` | `…翻炒。呃，放下去以后呢。` | **标点吞尾**（见第 3 节） |
| en_zh | 054 | merge | 两者都不是 | `Yeah .。` | `嗯` | `うん。` | **假名映射** `うん→Yeah.`（`711800a` 设计内） |

注意区分两件事：这张表说的是「最终文本取自哪一路」；第 4 节说的「谁更准」是另一回事。032 虽然取的是 online，但两条路都只错一处（offline 把「卫生间」听成「卫生巾」）。

### 2.6 为什么会误标

`app/dual_track.py:692-715`

```python
def _stamp_asr_obs(self, online, offline, merged):
    on = (online or "").strip() or None      # 原始文本，带标点
    off = (offline or "").strip() or None    # 原始文本，带标点
    m = (merged or "").strip()               # 已经被 _strip_asr_tail_punct 去过尾部标点
    ...
    elif m == off:  "offline"
    elif m == on:   "online"
    else:           "merge"                  # ← 只要尾部有句号，就落到这里
```

两个错叠在一起：

1. **`m` 已去尾部标点，`on`/`off` 没有。** 只要原文尾部有句号，`m == off` 永不成立 → 掉进 merge。
2. **`m` 去标点后可能恰好等于 `on`（流式定稿本来就不出标点）。** 于是「实际选了 offline」被标成 online。`m == on` 这一支比看起来危险，它会**把 offline 的功劳记到 online 头上**——这正是 2.3 里 206 条错标的主要来源。

举例：

```
# 误标成 merge（Dev 036）
on : 有什么舞蹈呢拉丁舞啊            （无标点）
off: 有什么舞蹈的？拉丁舞啊。        （有标点）
src: 有什么舞蹈的？拉丁舞啊？        ← 跟 off 内容一样
pick: merge                          ← 应该 offline

# 误标成 online（Dev 001）
on : 可找一些不一样的活动
off: 可找一些不一样的活动。
src: 可找一些不一样的活动。          ← 来自 offline
pick: online                         ← 应该 offline（本条两路一致，标错也无害）
```

---

## 3. 证据三：`asr_hyp` 不是 ASR 原文，是标点模型的输出

`app/dual_track.py:1330-1347`

```python
display_src = full
if bool(getattr(config, "ASR_PUNCT_DISPLAY", False)):
    puncted, punct_ms = add_punctuation(full)
    display_src = dedupe_display_punct(puncted or full)   # ← 标点模型重写
...
self.stats.source = display_src                            # ← 落盘 / 评测 / 字幕都用它
```

而 MT 吃的是 `full`（`:1329` `self._translate_final(full, ...)`）。

**所以 `stats.source` 是"给人看的文本"，不是"ASR 说了什么"。**

Test 027 就是活证据：

```
MT 译文: "Some onions, ... Uh, put it down later."   ← 有 "put it down later"
展示 src: "…要不断的翻炒。"                            ← 没有"放下去以后呢"
ASR 原文: "…翻炒。呃，放下去以后呢。"                   ← 有
```

标点模型把以「呃」开头的尾句整段吃掉了。MT 不知道，还在译；字幕和评测看到的是残句。

规模：**两集共 120 条里只此 1 条**（Test 027），Dev 0 条，en_zh 0 条。所以不是系统性问题，但会让 CER 虚高——这一条旧口径算 0.52，新 `core` 口径算 0.30，而按 ASR 原文只有 0.04。

---

## 4. 顺带：现策略基本是对的，天花板只有 +2 条

我把 zh_en 单 utt 的条目（Test 47 / Dev 46）逐条比了两路：

| 集 | n | online 更好 | offline 更好 | 打平 | asr_ok(on) | asr_ok(off) | **oracle 上界** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Test | 47 | 2 | 16 | 29 | 43 | 44 | **45** |
| Dev | 46 | 4 | 12 | 30 | 43 | 42 | **43** |
| 合计 | 93 | 6 | 28 | 59 | 86 | 86 | **88** |

- offline 更好的 28 条，绝大多数是**句末完整**（online 流式定稿常截尾：`怎么` / `一` / `快快` / `难`）
- online 更好的 6 条里，**4 条是真听错**：Test 008（off 丢掉"有"）、Test 046（off 把「难过」听成「南国」）、Dev 036（「呢」听成「的」）、Dev 056（「先」听成「现」）；另外 2 条只是数字写法（`40` vs `四十`、`3月21号` vs `三月二十一号`）
- **oracle（每个 utt 都按最优选）比现策略只多 2 条 = 2.2%**

所以结论不是"offline 永远对"。Test 046 就是选错了的那一条——online 一字不差，offline 把「难过」听成「南国」，而策略仍然选了 offline。但这类情况在 93 条里只有 4 条，而 oracle 能额外救回的只有 2 条。

**「把仲裁做好」的天花板是 +2 条，现策略已经拿到 86/93。这个方向不值得再投入。**

---

## 5. 本轮做三件事（都在观测层）

### 5.1 修 `arbiter_pick` 误标

改 `app/dual_track.py`：

1. `_stamp_asr_obs()` 内部增加一个规范化比较，比较前统一去掉所有标点与空白：

```python
def _norm_cmp(t: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", t or "", flags=re.UNICODE).lower()
```

2. **在 `:1347` 之前再调一次，用最终的 `display_src` 覆盖**：

```python
# :1234 那一次保留不动（兜底）
raw_final = (final.text or "").strip()
self._stamp_asr_obs(obs_on, obs_off, raw_final)

# :1347 之前新增一次，用 display_src 覆盖
self._stamp_asr_obs(obs_on, obs_off, display_src)
self.stats.source = display_src
```

**为什么保留两次而不是挪走**：`:1256`（空识别）、`:1293`（非目标语/假名，跳过 MT）、`:1309`（过短，跳过 MT）三条路径会提前 `return`，走不到 `:1347`。只挪一次会让这三类 utt 的 `arbiter_pick` 变成 `None`，验收 B 的合计就对不上 279 了。

**为什么要用 `display_src` 覆盖**：`:1234` 那次比较的是 `raw_final`（标点模型之前），它会漏掉两层改动——
- 假名映射（Test 054：`うん` → `Yeah.`）在 `raw_final` 里还没发生，用 `raw_final` 比会误判成 `offline`
- 标点吞尾（Test 027）发生在标点模型里，用 `raw_final` 比也会误判成 `offline`

用最终的 `display_src` 覆盖一次，这三条例外才能如实落到 `merge`，验收 C 才对得上。

3. **类别只保留三类**：`online` / `offline` / `merge`。不要新增 `mapped`、`partial` 之类的细分——那 3 条例外让它们留在 `merge` 里，人肉看 id 就够，多一个类别就多一处能写错的地方。

**禁止**：不许改 `prefer_2pass_asr()` 的任何一行；不许改 `ASR_2PASS_MERGE_MODE`；不许为了凑分布去改 `prefer_final_asr()`。

### 5.2 `detail.jsonl` 并列加 `src_raw` 列

`scripts/eval_dialogue_mslt.py` 的 `_utt_dict()` 增加一个字段：

```python
def _utt_dict(finished) -> dict:
    return {
        ...
        "src_raw": getattr(finished, "src_raw", None),   # ASR 原文，标点模型之前
    }
```

`app/dual_track.py` 在 `:1330` 之前把 `full` 存到 stats 上（`self.stats.src_raw = full`）。**注意 `:1256` / `:1293` / `:1309` 三条提前 return 的路径也要各加一行**，否则那批 utt 的 `src_raw` 全是空的，验收 E 会比不出东西。`TranslateStats` 里加这个字段，默认 `None`。

同时 eval 侧算一列 `asr_err_raw`，用 `src_raw` 而不是 `src`。**判断阈值仍用 0.35，不新增阈值。**

**禁止**：不许把 `asr_hyp` 直接替换成 `src_raw`。旧列必须原样保留，两列并列报数。理由：一旦替换，`asr_ok` 会和历史数据不可比。

### 5.3 标点吞尾打日志

`app/dual_track.py:1335` 附近，`add_punctuation` 之后：

```python
display_src = dedupe_display_punct(puncted or full)
# 新增：标点模型吃掉内容时报警
_n_raw = re.sub(r"[^\w\u4e00-\u9fff]+", "", full, flags=re.UNICODE)
_n_disp = re.sub(r"[^\w\u4e00-\u9fff]+", "", display_src, flags=re.UNICODE)
if len(_n_raw) - len(_n_disp) >= 4:
    print(f"[punct drop {len(_n_raw)-len(_n_disp)}字] {full!r} → {display_src!r}")
    self.stats.punct_dropped = True
```

阈值取 4 个字，理由：`dedupe_display_punct` 正常会去掉重复标点，1–3 个字的差异属于标点归一，4 个字以上才可能是内容丢失。这是**观测阈值，不是产品阈值**，允许后续按统计结果调整，但本轮不许改。

`punct_dropped` 也要加进 `TranslateStats`（与 5.2 的 `src_raw` 同一处），默认 `False`。

---

## 6. 红线（与前面几轮相同，未放松）

- `VAD_TEN_THRESHOLD = 0.4` / `VAD_SILENCE_SEC = 0.45` / `VAD_SPEECH_RMS_MIN = 0.035`：一个都不许动
- `MIC_DENOISE` 保持 `False`
- 不许改 `ASR_2PASS_MERGE_MODE`（保持 `"offline"`）
- 不许动 offline / online 权重，不许碰 `ASR_LOOP_*` / `ASR_SPEC_*` / `prefer_2pass`
- 不许扩 TM，不许加 postedit 规则
- 不许为了好看修改任何实测数字

---

## 7. 验收（A–H，全过才算完成）

| # | 检查 | 命令 / 方法 | 期望 |
|---|---|---|---|
| A | pytest | `python -m pytest -q` | 全绿，且总数 ≥ 168 |
| B | `arbiter_pick` 新分布 | 重跑 eval，统计 `utts[].arbiter_pick` | Test zh_en `{offline:70, online:1, merge:2}`（73）；Test en_zh `{offline:64, online:1, merge:1}`（66）；Dev 两方向 `{offline:140}`（140） |
| C | 那 3 条 merge 逐条可解释 | 打印 Test 全部 merge 的 utt | 必须是 `zh_en 002`（partial 补全）、`zh_en 027`（标点吞尾）、`en_zh 054`（假名映射），每条附一句原因 |
| D | KPI 一个不变 | 对比新旧 `summary.json` | `mt_ok` / `asr_ok_le_0.35` / `asr_err` 四项，两集两方向**完全相同** |
| E | `src_raw` 与 `src` 的 diff | 两列**去标点后**逐行比 | 只有 Test 027 一条不同（`src_raw` 长 7 字）；其余全部相同 |
| F | `asr_err_raw` 变化面 | 统计 `|asr_err_raw - asr_err| > 0.01` 的条数 | ≤ 1 条 |
| G | 吞尾日志能触发 | 搜 eval 日志 | 至少出现 1 次 `[punct drop`，且只对应 Test 027 |
| H | 没偷改产品 | `git show --stat` | 只碰 `app/dual_track.py` 的观测字段 + `scripts/eval_dialogue_mslt.py` + `tests/`；**`asr_stream.py` / `config.py` 必须零改动** |

### 报数格式（照上一轮的规矩）

```
A pytest:        ___ passed
B pick 分布:     Test zh_en ____ / Test en_zh ____ / Dev ____
C 三条 merge:    ____（逐条 id + 原因）
D KPI 差异:      ____（必须写"0 项不同"或逐项列出）
E src_raw diff:  ___ 条
F asr_err_raw:   ___ 条 > 0.01
G 吞尾日志:      ___ 次
H git diff:      app/asr_stream.py 改动 ___ 行，app/config.py 改动 ___ 行
```

另外附一张「修正前后 pick 正确率」对照：拿 2.3 那张表的口径，自己数一遍两路不同的 utt 里 pick 对了几条。修正前应该是 **4/210**，修正后应该是 **207/210**（那 3 条 partial/吞尾/映射无法归类到 on 或 off，落在 merge 里算"不算错但也不算对"，单独列出）。这条不进 A–H，但必须出现在报告里。

---

## 8. 如果你认为我错了

按这个顺序回，别直接开写：

1. 先贴 `app/config.py:92` 的实际值。如果不是 `"offline"`，第 1 节整节作废，请直接指出。
2. 再按 2.3 的口径自己数一遍：两路不同的 210 条 utt 里，`arbiter_pick` 对了几条。如果你的数不是 4（我这边的数），第 2 节作废，请贴出你的统计脚本和结果。
3. 最后贴 `stats.source` 的赋值链（`:1329` 给 MT 的是什么、`:1347` 给 stats 的是什么）。如果两者是同一个变量，第 3 节作废。

三条都推翻不了，就按第 5 节做。做完只交第 7 节的表，不要额外发挥。
