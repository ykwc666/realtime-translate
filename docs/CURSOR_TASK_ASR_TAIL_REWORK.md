# 返工任务书：zh→en ASR 长尾（711800a 第 3 步）结论不成立

给 Cursor。仓库 `D:\xuanjian\realtime-translate`。

**本轮红线**：不许改 `VAD_TEN_THRESHOLD=0.4` / `VAD_SILENCE_SEC=0.45` / `VAD_SPEECH_RMS_MIN=0.035`；不许把 `MIC_DENOISE` 改成 True；**不许调 `ASR_2PASS_*` / offline 权重**（原因见下）。

---

## 0. 一句话

`711800a` 第 3 步输出：`n_asr_fail=10, offline_helps=2 → ALLOW_OFFLINE_AB`。

我用**同一份 `detail.jsonl`** 重算：那 10 条里只有 **2 条**是真正听错；而那 2 条上，online 和 offline **两路都错**。

所以 `offline_helps` 实际是 **0**，verdict 应该是 `STOP_MODEL_CEILING`。**下一轮不要调权重。**

不是你的代码有 bug，是**判定口径把「重复字」和「听错字」算成了同一类**。

**本轮产物只有诊断口径和报表，产品行为零改动，KPI 不会变。** 如果你交上来 KPI 变了，说明你改错了地方。

---

## 1. 我的测法（这一节是重点，请照这个复现）

### 1.1 数据从哪来

只有一份输入：`data/eval/dialogue_v2/detail.jsonl`（`scripts/eval_dialogue_mslt.py` 跑出来的，本地已有，不用重跑评测）。

每行我用到 6 个字段：

| 字段 | 含义 | 谁写的 |
|---|---|---|
| `direction` | `zh_en` / `en_zh`，本步只看 `zh_en` | eval 脚本 |
| `src_ref` | 中文参考（T2 层，已过 T1/T2 配对校验） | manifest |
| `asr_hyp` | 最终送入 MT 的识别结果（仲裁后） | eval 脚本 |
| `asr_online` | 流式 Paraformer 的假设 | `2cecf7a` 新增 |
| `asr_offline` | 离线 SenseVoice 的假设 | `2cecf7a` 新增 |
| `asr_err` | 现有 CER（字级编辑距离 / 参考长度，封顶 1.0） | eval 脚本 |

### 1.2 现有 `asr_err` 的问题

它是**纯字级编辑距离**。于是下面两件事被算成同一件事：

```
你有。      → 你你你有。                          CER 1.00
其实。      → Hereうん                            CER 1.00
```

第一句产品上完全能用（人一看就懂），第二句才是不能用。**CER 都是 1.00，无法区分。**

`711800a` 的 `offline_helps` 就是在这个 CER 上算的，所以它数的是"谁的编辑距离小一点"，不是"谁听对了"。

### 1.3 我加的口径：`asr_err_core`

定义三行，全部确定性、无模型、无随机：

```python
FILLER_ZH = set("嗯呃啊哎哦唉呀诶额噢喔")

def _core_zh(text: str) -> str:
    # 1) 去标点/空白：复用现有 _norm_zh_chars（保留汉字、字母、数字，转小写）
    t = "".join(_norm_zh_chars(text))
    # 2) 去语气词
    t = "".join(ch for ch in t if ch not in FILLER_ZH)
    # 3) 连续重复折叠成 1 个（你你你有 → 你有）
    return re.sub(r"(.)\1+", r"\1", t)

def asr_err_core(direction: str, ref: str, hyp: str) -> float:
    if direction != "zh_en":
        raise ValueError("本轮只处理 zh_en")
    r, h = _core_zh(ref), _core_zh(hyp)
    if not r:
        return 0.0 if not h else 1.0
    # 参考有多少内容「按顺序」出现在假设里；漏字/换字才算错，多字不算错
    return round(1.0 - _lcs_len(r, h) / float(len(r)), 3)
```

`_lcs_len` 就是最长公共子序列长度，标准二维 DP，30 行以内。

**三条设计理由，请照抄不要改**：

1. **折叠重复**：`你你你有` / `对对，我。就就两个` 这类逐字重复折叠成 1 个。折叠后 `008`、`045`、`029` 的 `asr_err_core` 为 **0.00**。
2. **去语气词**：`嗯，好的，嗯，拜拜。` / `哎，对了，嗯嗯呃，份子钱拿。` 里的"嗯/呃/哎"不是内容。注意：**只有当去掉后还剩内容时才去**，`其实。`→`其实` 保持不变。
3. **用 LCS 而不是编辑距离**：LCS 只惩罚"参考里的内容没出现"，不惩罚"假设多说了"。这是"听漏了"和"话多了"的分界。两个例子：`002`（整句说了两遍，逐字重复折叠管不到）从 1.00 降到 **0.00**；`007`（94 字中英混说，假设里多了 risky / adventure）从 0.61 降到 **0.03**。

**阈值仍然是 0.35**，一个字都没改，与现有 KPI 同口径，这样两套数才能并列看。

### 1.4 我实际跑的两条命令

```bat
cd /d D:\xuanjian\realtime-translate

:: ① 看 Test 的 10 条
.venv\Scripts\python.exe -c "import json;rows=[json.loads(l) for l in open('data/eval/dialogue_v2/detail.jsonl',encoding='utf-8') if l.strip()];[print(r['id'],r['asr_err'],repr(r['src_ref']),'->',repr(r['asr_hyp'])) for r in rows if r['direction']=='zh_en' and r['asr_err']>0.35]"

:: ② 看 Dev 的 13 条
.venv\Scripts\python.exe -c "import json;rows=[json.loads(l) for l in open('data/eval/dialogue_dev_v2/detail.jsonl',encoding='utf-8') if l.strip()];[print(r['id'],r['asr_err'],repr(r['src_ref']),'->',repr(r['asr_hyp'])) for r in rows if r['direction']=='zh_en' and r['asr_err']>0.35]"
```

然后对每一条，用 1.3 的 `asr_err_core` 重算 `src_ref` vs `asr_hyp`、`asr_online`、`asr_offline` 三列，逐行看。

### 1.5 我判"offline 赢"的条件（和你的脚本一致，没改）

```
off_err <= 0.35  且  on_err > 0.35   → offline 赢，+1
```

唯一区别是 `off_err` / `on_err` 换成 `asr_err_core`。

---

## 2. 我跑出来的数

### 2.1 Test（60 条 zh_en，10 条 `asr_err>0.35`）

| id | 参考 | 最终识别 | 旧 CER | 新 `core` | 这到底是不是听错 |
|---|---|---|---:|---:|---|
| 008 | 你有。 | 你你你有。 | 1.00 | **0.00** | 不是，重复 |
| 002 | 老外也是这么认同的。 | 老外也是老外也是这么认同的。也是这么认同的。 | 1.00 | **0.00** | 不是，整句重复 |
| 045 | 好的。拜拜。 | 嗯，好的，嗯，拜拜。 | 0.50 | **0.00** | 不是，多两个"嗯" |
| 029 | 对。我两个都还蛮喜欢。 | 对对，我。就就两个都都还蛮喜欢。 | 0.44 | **0.00** | 不是，口吃 |
| 007 | 因为我觉得旅行……（94 字中英混说） | 同左，多了 risky / adventure | 0.61 | **0.03** | 不是，混说 |
| 053 | 对了。份子钱那 | 哎，对了，嗯嗯呃，份子钱拿。 | 0.83 | **0.17** | 半是，那→拿 1 字 |
| 024 | 这样可以买半成品。 | 啊，这样客气买。半成品。 | 0.38 | **0.25** | 是，可以→客气（2 字） |
| 027 | 一些葱……放下去以后呢？ | 些葱……放下去。要不断的翻炒。 | 0.52 | **0.30** | 不是，**语序乱了**，字都在 |
| **026** | 主要是纠正他们。 | 全儿全而纠正他们。 | 0.57 | **0.43** | **是**，前 4 字全错 |
| **059** | 其实。 | Hereうん | 1.00 | **1.00** | **是**，整句不成话 |

**新口径下仍失败的只有 2 条：026、059。**

### 2.2 Dev（60 条 zh_en，13 条 `asr_err>0.35`）

新口径下剩 **4 条**：

| id | 参考 | 最终识别 | 旧 CER | 新 `core` |
|---|---|---|---:|---:|
| 017 | 这个后来就没在养了。 | 嘿，后来就没再养。 | 0.44 | 0.44 |
| 028 | 是的。 | だ | 1.00 | 1.00 |
| 029 | 不错哎。那个三月二十一号是星期几啊？ | 啊，不错啊，那个那个3月21号，星期几啊？ | 0.56 | 0.36 |
| 053 | 是。对。 | Yeah，sure，对。 | 1.00 | 0.50 |

被旧口径误计的 9 条（新 `core` 全部 ≤0.26）：
`005=0.12  014=0.00  016=0.26  021=0.12  025=0.13  038=0.05  046=0.00  049=0.07  056=0.17`

> **已知小瑕疵，本轮不要修**：Dev `029` 的 0.36 来自"三月二十一号" vs "3月21号"的写法差异，不是听错。要消除它得加数字归一化规则，收益为零还会误伤"一些"。**本文档明确要求：不要为这一条加规则。**

### 2.3 `offline_helps` 新旧对照

| 集 | 旧口径失败数 | 旧 `offline_helps` | 新口径失败数 | 新 `offline_helps` | 谁赢 |
|---|---:|---:|---:|---:|---|
| Test | 10 | 2（027、029） | **2** | **0** | 无 |
| Dev | 13 | 0（056 是 online 赢） | **4** | **0** | 无 |

**关键在于：旧口径那 2 个"offline 赢"的条目，换成 `core` 之后两路都合格，谁也没赢。**

```
027  ref  一些葱，然后把这个土豆丝要不断的翻炒。放下去以后呢？
     on   别葱然后把这个呃土豆丝儿放下去要不断的翻炒呃放下去以后      core 0.130
     off  些葱，然后把这个嗯土豆丝儿放下去，要不断的翻炒。呃，放下去以后呢。 core 0.043
     → 两路都 ≤0.35，都"合格"。按 1.5 的规则（要 on>0.35 才计数），这条不计分。
       它的问题不是听漏，是"放下去"被挪到了"翻炒"前面——**语序**，换权重改不了。

029  ref  对。我两个都还蛮喜欢。
     on   就就会两个都都还蛮喜欢                                core 0.222
     off  就就两个都都还蛮喜欢。                                core 0.222
     → 两路完全相同地少"对我"、多"就会"，**打平**。折叠重复后差异只剩这一处，
       说明 off 的"优势"完全来自重复字被计入编辑距离。
```

而真正听错的两条：

```
026  on 全儿侄儿纠正他       core 0.57   错
     off 全儿全而纠正他们。   core 0.43   也错
059  on 就是                core 1.00   错
     off Hereうん。          core 1.00   也错
```

**所以"改 offline 权重"能救的条目数是 0。** 不是"收益小"，是这 10 条里没有一条会因为它变好。

---

## 3. 你要做的四件事

### 3.1 把诊断脚本搬进仓库（必做，第一件）

现在生成 `zh_en_asr_tail.json` 的脚本在 `cache/diag_zh_en_asr_tail.py`，而 `.gitignore` 里有 `cache/`。

**也就是说：`ALLOW_OFFLINE_AB` 这个结论，从任何一个 commit 都复现不出来。** clone 下来的人只能看到 json，看不到生成它的代码。

要做：
- 移到 `scripts/diag_asr_tail.py` 并提交
- 加参数：`--detail <path>`（默认 `data/eval/dialogue_v2/detail.jsonl`）、`--out <path>`（默认 `data/eval/dialogue_v2/zh_en_asr_tail.json`）
- 两个集都能跑，不再硬编码单一路径

### 3.2 `detail.jsonl` 加一列 `asr_err_core`（必做）

- 位置：`scripts/eval_dialogue_mslt.py`，`run_one` 里紧挨着 `"asr_err": round(err, 4)` 那行
- 只加列，**不改 `asr_err`**
- **`summary.json` 一个字段都不许动**：`asr_ok_le_0.35` / `asr_ok_rate` 仍然用 `asr_err`。`asr_err_core` 是诊断列，不进 KPI。

### 3.3 重出 tail json（必做）

字段要求：

| 字段 | 值 |
|---|---|
| `n_asr_fail` | 用 `asr_err_core > 0.35` 数 |
| `n_asr_fail_kpi` | 用 `asr_err > 0.35` 数（保留，方便对照） |
| `offline_helps` | 用 `asr_err_core` 算 |
| `offline_helps_kpi` | 用 `asr_err` 算（保留） |
| `verdict` | **用 `offline_helps`（新口径）判** |
| `rule` | 写清"用 asr_err_core，不是 asr_err" |
| `rows[].core_err` | 新增，该行 `asr_err_core` |
| `rows[].on_core` / `off_core` | 新增 |
| `rows[].kind` | 新增，取值见下 |

`kind` 三分类（给人和给机器都用得上）：

```
if core(ref) 为空                -> "empty_ref"
elif asr_err_core == 0.0         -> "redundant"    # 内容全在，只是多了字
elif asr_err_core <= 0.35        -> "partial"      # 内容大部分在：个别字错 / 语序乱
else                             -> "misheard"     # 内容真丢了
```

注意 `partial` 是个**混合桶**，本轮里面既有"1 个字听错"（Test 053 那→拿）也有"语序乱"（Test 027）。它能回答"要不要换权重"（不能，因为两路都合格），但**不能用它解释原因**，别在报告里把 partial 一律叫"语序问题"。

顺便删掉 `both_wrong_diff` 这个字段：它数的是 `online != offline` 的**字符串**差异，不是判断差异，本轮 8 条里多数是差一个"嗯"字。留着会误导。

### 3.4 加测试（必做）

`tests/test_asr_core.py`，纯字符串断言，不加载模型，必须 0.1 秒跑完：

```python
@pytest.mark.parametrize("ref,hyp,want", [
    ("你有。", "你你你有。", 0.0),
    ("好的。拜拜。", "嗯，好的，嗯，拜拜。", 0.0),
    ("对。我两个都还蛮喜欢。", "对对，我。就就两个都都还蛮喜欢。", 0.0),
    ("主要是纠正他们。", "全儿全而纠正他们。", 0.429),
    ("其实。", "Hereうん", 1.0),
    ("对了。份子钱那", "哎，对了，嗯嗯呃，份子钱拿。", 0.167),
    ("", "", 0.0),
])
def test_asr_err_core(ref, hyp, want):
    assert asr_err_core("zh_en", ref, hyp) == pytest.approx(want, abs=0.002)
```

另外加一条**防回归**断言：`asr_err` 的行为不许变。

```python
def test_asr_err_unchanged():
    # 旧口径必须保持字级编辑距离，防止有人"顺手统一"
    assert asr_error("zh_en", "你有。", "你你你有。") == 1.0
```

---

## 4. 我怎么验收（我会照着跑）

| # | 命令 / 检查 | 期望 |
|---|---|---|
| A | `git ls-files scripts/diag_asr_tail.py` | 有输出（说明已入库） |
| B | `pytest tests/test_asr_core.py -q` | 全绿，<1s |
| C | `pytest -q` | 全绿（现 159 passed，加新测试后 ≥167） |
| D | Test tail json `n_asr_fail` | **2**；`n_asr_fail_kpi` = 10 |
| E | Test tail json `offline_helps` | **0**；`offline_helps_kpi` = 2 |
| F | Test tail json `verdict` | `STOP_MODEL_CEILING` |
| G | Test tail `rows[].id` 集合 | 恰好 `{026, 059}` |
| H | Dev tail json `n_asr_fail` | **4**；`offline_helps` = 0；`verdict` = `STOP_MODEL_CEILING` |
| I | `git diff` 里有没有 `app/asr_stream.py` / `app/dual_track.py` / `app/config.py` | **必须没有** |
| J | `summary.json` 的 `mt_ok_rate` / `asr_ok_rate` | 与 `711800a` 完全一致（V2 Test zh_en 47/60、en_zh 53/60；Dev 47/60、46/60） |

D–H 这五条，如果和我的数对不上，**不要改代码凑数**，把逐行差异发我，我们对表。

---

## 5. 本轮明确不做

- 不调 `ASR_2PASS_*` / offline 权重 / `ASR_LOOP_*` / `ASR_SPEC_*` / `prefer_2pass`
- 不动 VAD 三个阈值、不开 `MIC_DENOISE`
- 不给 Dev `029` 加数字归一化规则
- 不改 `mt_usable()` / `mt_usable_sem()` 的阈值
- 不加新的 TM 条目、不加新的 postedit 词
- 不重跑 `eval_dialogue_mslt.py` 的模型推理部分去"顺手优化"（本轮只改诊断与报表）

---

## 6. 如果你认为我错了

按这个顺序反驳，别直接改代码：

1. 贴出**逐行**对照：id / `src_ref` / `asr_hyp` / 你算的 `asr_err_core` / 我算的
2. 指出三条定义里哪一条不成立（折叠重复 / 去语气词 / LCS），并给一个反例
3. 说明为什么这个反例会改变"026、059 两路都错"这个事实

只要这两条事实不变，`offline_helps=0` 就成立，权重就不该动。

---

## 附：本轮不在本文档范围内、但记账要清的三件事

1. `711800a` 的 5 条 TM（`Yeah→是啊`、`Nice→很好`）选词时考虑了 MSLT 参考的词面（报告自己写了"能过词袋闸"）。建议在 `data/mt_tm.jsonl` 对应条目的 note 里写明，别以后被当成"自然度最优"的证据。
2. `mt_ok_rate_sem` 的定标一致率是 **75%**（不是 85%）；`029`（`two of them are pretty much the same` vs `I quite like both`）是明确误判，sem 口径救回的 11 条里有 1 条不该救。作为并列口径可以接受，但别把它当成绩单。
3. **仲裁合并（`arbiter_pick=merge`）有时比单路更差。** 全量扫下来 3 条，`core` 差值 >0.10：

   ```
   Test 027  fin 0.304  on 0.130  off 0.043   ← 合并后比两路都差
   Dev  029  fin 0.357  on 0.071  off 0.357
   Dev  036  fin 0.111  on 0.000  off 0.111
   ```

   3/120 条，比例不高，但方向明确：合并逻辑不是"取优"。这是 `arbiter` 的问题，**不是 ASR 权重的问题**，本轮不改，只记账——下一轮如果要提 KPI，这 3 条比调权重更值得看。
