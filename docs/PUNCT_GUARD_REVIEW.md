# PUNCT_GUARD 独立复核（`275e432`）

复核方式：只读 diff + 重算 `detail.jsonl` + 对比上一跑日志 + 跑 pytest。没有改代码。

## 0. 结论

**过。** 守卫只在一处触发（Test 027），字幕把那半句带回来了，`asr_ok` 50→51，`mt_ok` 在 zh_en 两集一条没动。

有一件事要让 Cursor 知道：**他报的 Dev en_zh `mt_ok 46→45` 不是这刀的锅**，我定位到了具体那条（`005`），是识别文本两次跑不一样。附带说明：我验收表里写的"`mt_ok` 一条都不许变"对 en_zh 是不成立的要求，这条要改。

---

## 1. A–H（我的复算）

| # | 报告 | 我复算 | 结论 |
|---|---|---|---|
| A | 177 passed | `177 passed in 103.56s` | 过 |
| B | Test zh_en 0.2365→0.2329、asr_ok 50→51；Dev zh_en 不变 | 完全一致；另 `coverage 0.9953→0.9968`、`given_asr_n 50→51`（027 的 MT 仍判不过，所以 given 率 0.86→0.8431，正常） | 过 |
| C | 47 / 47 / 53 / 45 | 一致；45 的原因见 §2 | 过（口径要改） |
| D | 展示变短 0 条 | 两集 279 条 utt 全部 `len(src) >= len(src_raw)`，027 差 0 | 过 |
| E | fallback 1 / dropped 1 | Test 027 fallback；Dev 0；`punct_dropped` 仍 1 | 过 |
| F | `{offline:71, online:1, merge:1}`；210/210 | 一致；两路不同 210 条，对 210、错 0、例外 0（027 现在落 offline，002 仍 merge 且不计入分母） | 过 |
| G | guard 1 / drop 1 | 一致，Dev 两样都是 0 | 过 |
| H | asr_stream 0 / config 0 | 一致；只碰 `dual_track.py`(30/11) + eval 1 行 + 新测试 45 行 + 3 份文档 | 过 |

实现复核：`pick_display_src()` 是纯函数，空串和 `drop>=4` 都回退；`punct_dropped`（检出）与 `punct_fallback`（已回退）分开记，语义没混。4 条单测覆盖 0 字 / 3 字 / 7 字 / 空输出。**没有发现"回退后又被别处改写"的路径**——`:1398` 那次 `_stamp_asr_obs` 用的就是回退后的 `display_src`。

---

## 2. Dev en_zh `mt_ok 46→45`：定位到 `005`，与守卫无关

守卫在 Dev 触发 **0 次**，从代码路径上不可能影响 Dev。我去比了两跑的 utt 文本，Dev 只有 3 条有差异，其中唯一进失败集的是 `mslt_en_zh_005`：

```
上一跑 hyp: Yeah, just give me a sec, Vic. I'm going to go check because it's been 30 minutes.
本  跑 hyp: Yeah , just give me a sec Vic .。I'm gonna go check，'cause it's been 30 minutes。
参考      : Yeah, well, just give me a sec. They're coming to check 'cause it's been thirty minutes.
判  定    : mt=awkward，sem=ok
```

同一段音频，2pass 的 offline 假设一次给 "going to … because"，一次给 "gonna … 'cause"。**这是投机解码快照抖动**（`app/dual_track.py:632-660` 快照长度依赖线程调度），不是守卫改的。注意 `sem` 判的是 ok——这条严格判定本来就在边界上。

另外两条 Dev 差异是 `zh_en_028` 多出一条 `だ`、`en_zh_028` 多出一条 `まい`（假名噪声 utt，跳过 MT，不影响 mt_ok）。

**我验收表的错误**：我写了"`mt_ok` 一条都不许变"，但同时承认 en_zh 有 ±1 噪声。这两句自相矛盾——`mt_ok` 是从 `src` 推出来的，`src` 会抖，`mt_ok` 必然跟着抖。从现在起口径改成：

> **zh_en 两集 `mt_ok` 一条不许变；en_zh 允许 ±1，但必须能指到具体某条 utt 的识别文本变了（贴 old/new 两行）。**

---

## 3. 一个容易误读的地方："变化面只有 027" 是 KPI 层面

KPI 上确实只有 027 变了。但 utt 文本层面，Test 有 6 条、Dev 有 3 条与上一跑不同（`059` 从空识别变成 `Hereうん`、`045` 变成 `W`、`018` 多出 `あ、エせ`、`024`/`034` 措辞变了）。这些都没进 KPI，因为错误率本来就顶格（1.0）或没被计入。

**结论不变，但以后说"没变"要写清是哪一个层面。**

---

## 4. 守卫的边界（记一笔，不是缺陷）

- 阈值是**字符数**，不是词数。英文里 ≤3 字母的短词（`too` / `not` / `you`）被吃不会触发回退。我查了 `034`（疑似丢了 "too"），发现 `src_raw` 里本来就没有——是识别假设不同，不是标点吃的。**目前没有一例"3 字以内真被吃"的证据**，所以不动阈值。
- 回退后展示文本保留流式模型自己的标点（027 现在结尾没有句号）。这是有意的：少个句号比少半句好。

---

## 5. 现在的状态

```
Test zh_en  asr_ok 51/60 (85.0%)  mt_ok 47/60 (78.3%)  asr_err 0.2329
Dev  zh_en  asr_ok 47/60 (78.3%)  mt_ok 47/60 (78.3%)  asr_err 0.2294
Test en_zh  asr_ok 47/60 (78.3%)  mt_ok 53/60 (88.3%)  asr_err 0.2309
Dev  en_zh  asr_ok 46/60 (76.7%)  mt_ok 45/60 (75.0%)  asr_err 0.2267
```

`pytest 177 passed`，`asr_stream.py` / `config.py` 零改动，VAD 三阈值与 `MIC_DENOISE` 未动。

剩下的都是已知天花板：zh_en 2 条真听错、en_zh 失败集中在 ≤4 词碎片、仲裁 oracle 只多 1–2 条。**代码层面没有"再拧一下涨一截"的活了。**

下一刀按原计划不是代码：锁基线 → 打 tag + 已知限制 → 实麦冒烟。
