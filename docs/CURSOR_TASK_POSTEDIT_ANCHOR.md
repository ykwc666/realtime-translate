# 评审：会后重译（2026-11-05）

结论：链路能跑、能出稿、多块也不丢内容。但有 **1 个静默删内容的真 bug**、**1 个恒真闸门**、
**2 处证据不成立**。修完再谈字幕。

---

## 1. P0 真 bug：`postedit_zh_en` 子串命中就返回整句，删掉其余内容

### 现场

`data/sessions/20261105-160805/transcript.md` 第 3 行：

```
| 7.1–13.9 | 现在是星期二，他现在没有回我的消息。 | He hasn't replied to my messages yet. |
```

原文两个子句，译文只剩一个。「现在是星期二」没了。

### 复现

```
MT raw       : It is Tuesday and he has not replied to my message now.   ← 模型是对的
postedit 之后 : He hasn't replied to my messages yet.                    ← 被替换掉
```

### 根因

`app/mt_postedit.py:207`：

```python
if re.search(r"回我的?消息", src):        # 子串命中
    ...
    return ("He hasn't replied to my messages yet." if neg else "...")   # 直接返回整句，丢掉 hyp
```

规则设计时假设「源句就是这一句」，但用的是 `re.search`，只要句子里**包含**这几个字就整句替换。
`hyp`（MT 的真实输出）被完全丢弃。

### 不是孤例（同一条函数里 4 个规则，3 个中招）

| 输入 | postedit 输出 | 丢掉了 |
|---|---|---|
| 我觉得听不清，你能再说一遍吗？ | `I can't hear clearly.` | 「你能再说一遍吗」 |
| 今天天气怎么样？我下午想出去走走。 | `How's the weather today?` | 「我下午想出去走走」 |
| 现在是星期二，他现在没有回我的消息。 | `He hasn't replied to my messages yet.` | 「现在是星期二」 |

### 影响面

`Translator.translate()` 里 `postedit_zh_en(text, raw)` 是**实时与会后共用**的（`app/translate.py:100`）。
实时路径只要 VAD 把两个子句并进一句，就会中招。这不是"会后才有"的问题。

这比 WER 0.59 更伤：WER 差是"听得不准"，这个是"听准了却把话删了"，而且用户看不出来。

---

## 2. P1 闸门恒真：分块路径在 G1/G2 里一次都没被执行

`data/ledger_corpus` 实测时长：

```
n=60  min≈1s  p50=3.9s  max=11.5s   >25s: 0 条
```

默认块长 25s → **每条语料恰好 1 块** → 走的就是 oneshot。所以
`wer_post = 0.1381 = wer_oneshot，Δ=0.0` 不是"通过"，是"没测"。
样例 session 的 `report.json` 同样是 `chunks: 1`。

### 补测（129s 拼接，30 条语料，0.6s 间隔）

| 块长 | chunks | WER | Δ vs oneshot(0.1287) |
|---|---|---|---|
| 15s | 9 | 0.1314 | +0.003 |
| 25s（默认） | 6 | 0.1743 | **+0.046** |
| 50s | 3 | 0.1850 | +0.057 |
| 90s | 2 | 0.2547 | **+0.126** |

结论三条：

1. **多块能跑通，不丢内容**（chunks 与 report 一致，边界落在静音上）。
2. **G2 的 `+0.02` 容差在 6 块时已被突破**（+0.046）。这个阈值只在单块下成立，没有跨块数校准。
3. **块越长越差**（90s 掉到 0.2547）。别把块拉长来"减少边界"——方向是反的。

> 注意：这是 30 条不同来源语料拼起来的**补丁音频**，边界不自然。
> 15s vs 25s 的差（+0.003 vs +0.046）不足以支撑改默认，**不要为这个再开一轮扫描**。
> 但"≤30s、别超 60s"这个上界是有证据的。

---

## 3. P2 G1 被降级

`scripts/gate_offline_retranscribe.py` 输出：

```json
{"n": 60, "empty": 0, "char_ratio_lt_085": 2, "g1_empty_ok": true}
```

闸门原文是「逐条 ≥0.85 且空输出=0」。实际有 2 条 <0.85，但布尔只判了 `empty==0`，
另外 2 条被写成「辅证」，且**没有输出是哪 2 条**。

要求：把低于阈值的条目连 `ref`/`hyp`/ratio 一起打出来。差 0.03 是阈值太紧，差 0.5 是真丢内容——
现在分不清。

## 4. P3 样例的 `live_compare` 不成立

`report.json`：

```json
"live_compare": {"live_utts": 1, "post_segments": 3, "live_missed": 2, "live_wrong": 0}
```

`live.jsonl` 只有 1 行，`mode: "file"`, `source_desc: "smoke"` —— 是别的冒烟跑留下的残留，
不是这次 session 的实时稿。所以「会后补回 2 句」是假的。

同时 `meta.json` 是 `mode: "file"`：样例是拿 `accept_phrases.wav` 灌进去的，
**`--record` → 实麦 → 会后** 这条链没有任何一次端到端证据（虽然 `pipeline.py:193` 已接线）。

## 5. P4 零单测

`tests/` 里没有任何 `retranscribe` 相关用例。`split_chunks` / `split_sentences` /
`_alloc_timestamps` / `compare_live` 全是新代码，全靠手工跑样例验证。

---

# 任务：修 postedit 锚定 + 补多块闸门

## 红线

- 不改 VAD / ASR 任何默认值，不动 `dual_track` 切句
- 不改 `docs/OFFLINE_RETRANSCRIBE.md` 里已公布的产物格式（只加字段不加改名）
- 单变量：本轮**只**修 postedit 与闸门，不顺手改块长

## P0 postedit 锚定（必须）

对 `postedit_zh_en` / `postedit_en_zh` 的**每一条** canned 规则：

1. 改成 `re.fullmatch`，把规则本来就隐含的前后缀写进模式（如 `^(?:啊)?(?:现在)?(?:他|你|我)?(?:还)?(?:没有|没)?回(?:我的|我)?消息[。？！?!]?$`）
2. 加一道**兜底闸**：源句含子句分隔符（`，,；;`）时，跳过所有 canned 替换，直接返回 `hyp`
3. 加第三道闸：若替换后词数 < `hyp` 词数的 50%，返回 `hyp`（防以后新规则再犯）

`postedit_en_zh` 同一条病（`repl(?:y|ied)\s+to\s+my\s+messages?` 也是子串命中），一起修。

### 测试（表驱动，必须覆盖反向）

| 用例 | 期望 |
|---|---|
| `他现在没有回我的消息。` | 命中规则，返回 canned |
| `现在是星期二，他现在没有回我的消息。` | **不命中**，返回 `hyp` |
| `我觉得听不清。` | 命中 |
| `我觉得听不清，你能再说一遍吗？` | **不命中**，返回 `hyp` |
| 无关键字的句子 | 原样返回 `hyp` |

再加一条不变量测试：**对任意输入，`postedit(x, hyp)` 不得比 `hyp` 少 50% 的词**（用现有 ledger 语料遍历一遍做冒烟）。

## P1 多块闸门（必须）

在 `scripts/gate_offline_retranscribe.py` 里新增 G6：

1. 把前 30 条语料按 0.6s 间隔拼成一条 ~130s 音频
2. 断言 `chunks >= 5`（证明分块路径真被执行）
3. 断言 `WER(multichunk) <= WER(per-file oneshot) + 0.06` —— 阈值按实测 25s 块的 +0.046 留余量，
   **不要写 +0.02**（那是个体单块的数字，6 块必然过不了）
4. 输出每条边界的落点（秒）与落点前后 100ms 的 RMS，证明切在静音上

G2 保留，但改名 `G2_single_chunk` 并在报告里注明「只对单块成立」。

## P2 G1 明细（必须）

`char_ratio < 0.85` 的条目逐条打印 `ref / hyp / ratio`，并把数量写进布尔判据
（`g1_ok = empty==0 and n_low<=2`，阈值写清楚，别再用"辅证"绕过）。

## P3 `live_compare` 只用同一 session 的数据

- 读 `<session>/live.jsonl`；不存在或行数 0 → `live_compare: null`，**不要**去读 `data/harvest/`
- `meta.mode` 不是 `vad`/`duplex` 时，`live_compare` 一律 `null`（file 灌录没有实时稿）

## P4 三条单测（必须）

`tests/test_retranscribe.py`：

1. `split_chunks` 在 130s 静音/语音交替音频上，块数正确、每块 ≤ 1.2×target、边界落在低能量处
2. `split_sentences` 中英标点混合切分
3. `_alloc_timestamps` 首尾相接、总长等于块长

## 交付

1. 提交：`fix(mt): anchor postedit rules; gate multi-chunk retranscribe`
2. `docs/OFFLINE_RETRANSCRIBE.md` 追加「闸门修订」一节（G2 改名、G6 新增、G1 明细）
3. 一次**实麦**端到端：`run.py --mode vad` 说 5 句 → `run.py --post latest` → 贴 `transcript.md`
   （样例必须是 `mode: vad`，不接受 file 灌录）
4. 修完重跑 `eval_ledger --skip-a5`：AMI/FLEURS 的 ok 数与 WER **允许变化**（postedit 松手后可能升高也可能降低），
   但必须在文档里如实记录新数字，**不许为了保住 27/48 而保留删内容的规则**
