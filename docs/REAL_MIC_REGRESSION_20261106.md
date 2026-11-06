# 实麦回归验收 2026-11-06（只验不改）

基线：HEAD `42e83be` / 行为 `002e63a`。红线未动。  
工具：dualsim 文本可信、**时序不可信**（勿评延迟）。

**本轮结论：核心回归 PASS（postedit / 小数 / 单句），A0 实麦 PASS（`20261106-130636`）；两项未触发，不影响收口。**

---

## A 机器可验

### A1 素材

| 项 | 值 |
|---|---|
| 源 | `data/sessions/20261105-163258/mic_raw.wav`（255.9s） |
| 切片 | `[60,240]s` → `cache/mic_163258_trim.wav`（180.0s，16k mono） |
| 说明 | 该段**没有**「他现在没有回我的消息」；含你好/延迟、天气+星期二并句、审批会、方案、发文件。 |

### A2 回放

```text
.venv\Scripts\python.exe run.py --mode dualsim --file cache/mic_163258_trim.wav --no-tts
→ session data/sessions/20261106-130636
```

> dualsim **不落**可用 `mic_raw`（空壳）。若要对 dualsim session 做会后重译，请用 `--file` 指定原音频，或先把 wav 拷进 session（见提案 P3）。

### A3 今日 `live.jsonl`（20261106-130636）

| # | src（展示） | tgt | sub_lines | 备注 |
|---|---|---|---|---|
| 1 | 嗯 | （空，过短跳过 MT） | null | 与昨日一致 |
| 2 | 你好，，延迟大概有多少？ | Hello. How much delay is it? | null | `，，` 混排 |
| 3 | 今天天气怎么样？？现在是星期二。 | How's the weather today? It's Tuesday. | **2 条成对** | A0 触发；`？？` 混排 |
| 4 | 我明天上午10点要去客户那边开一个关于支付链路的审批会，大概2个小时。 | …payment chain, about two hours. | null | 完整 |
| 5 | 我们需要把这个方案再对一遍，，然后发给产品。 | We need to re-examine… | null | `，，` |
| 6 | 帮我把这个文件发给他，，再确认一下时间。 | Send him this file… | null | `，，` |
| 7 | The。 | 缩 | null | trim 尾噪声，可忽略 |

检查结论：

1. **半句吞没**：有效中文句未见半句被吞；「星期二」在 src / tgt / sub_lines 均在。  
2. **sub_lines**：仅第 3 条触发；`src/tgt` 成对。  
3. **translated**：`"How's the weather today? It's Tuesday."` 含两子句译文。  
4. **展示标点**：见 `？？`、`，，`（只记不修）。

### A4 与昨日并排（同段音频关键：天气+星期二）

| | 昨日 live `20261105-163258` | 今日 dualsim `20261106-130636` |
|---|---|---|
| src | 今天天气怎么样？？现在是星期二。 | 同左（展示层） |
| tgt | **How is today? It's Tuesday.**（run-on 整句 MT） | **How's the weather today? It's Tuesday.** |
| sub_lines | （当时无字段） | `[{天气…},{星期二…}]` |
| mt_triggers | 1 | **2** |

昨日 offline `transcript.md` 已把天气/星期二拆成两行；昨日 **live** 并句质量差但不算「删掉星期二」。  
今日 live：**星期二保留且分句 MT 质量更好** → A0 对这条并句 **PASS**。

（对照：真正「星期二被 postedit 吃掉」的是另一会话 `20261105-160805` 复合句，不在本 wav。）

### A5 `--post data/sessions/20261106-130636`

验收时曾手工 `Copy-Item cache/mic_163258_trim.wav → …/mic_raw.wav` 后：

```text
.venv\Scripts\python.exe run.py --post data/sessions/20261106-130636
→ segments=4 → transcript.md
```

| t0–t1 | 会后原文 | 会后译文 |
|---|---|---|
| 98.2–121.0 | 你好，延迟大概有多少？ | Hello. How much delay is it? |
| 121.0–125.5 | 今天天气怎么样？ | How's the weather today? |
| 125.5–148.4 | 现在是星期二，我明天上午10点要去客户那边开一个关于支付链路的审批会大概2个小时。 | It is Tuesday, and I have a meeting… |
| 148.4–171.8 | 我们需要把这个方案再对一遍，然后发给产品，帮我把这个文件发给他，再确认一下时间。 | We need to repeat… and confirm the timing. |

- **未丢「星期二」关键字**。  
- 会后行数少于 live 有效句块：离线能量并段，**不是 A0/postedit 回退**（观察项）。

### A6 汇总（dualsim 段）

| 指标 | 结果 |
|---|---|
| A0 触发次数 | **1**（天气+星期二） |
| postedit 命中（本段） | **0**（无「回我的消息」canned） |
| 是否丢「星期二」 | **否** |
| A 段总评 | **PASS**（A0 实麦/回放路径） |

### A8 live / post 双向对照（实麦 session `20261106-131540`）

`transcript.md` / `transcript.jsonl` 由对该目录执行 **`--post` 补跑**生成（进程曾非正常退出，缺 `meta.json`，见提案 P2）。

| 路径 | 含「星期二」的关键句 | 译文是否保留星期二 |
|---|---|---|
| **live** 第 6 意图 | `但是星期二，他现在没有回我的消息。` | ✓ `But Tuesday, he hasn't replied…` |
| **post** | `现在是星期二，他现在没有回我的消息。` | ✓ `It is Tuesday and he has not replied…` |
| **post**（另段粘连） | `…现在是星期二…现在是星期五，他现在没有回我的消息。` | ✓ 仍含 Tuesday（并夹 Friday） |

**双向结论：live 与 post 两路均未因 postedit 丢掉「星期二」。**

### 待确认（用户）

`20261106-131540` 第 4 句附近出现「星期五」：

- live：独立 utt `星期五。` → `Friday`
- post：长段原文含「现在是星期五」

**请确认：是本人当时所说，还是 TTS 外放串进麦克风？** 未确认前不当作产品 bug 立项。

---

## B 实麦（session `20261106-131540`，mic_raw≈2.0MB）

### B1 脚本（已跑）

| # | 句子 | 测什么 |
|---|---|---|
| 1 | 你好 | 最短句 |
| 2 | 延迟大概有多少 | 正常路径 |
| 3 | 今天天气怎么样 | 单句不得被误拆 |
| 4 | 现在是星期二 | canned 不得误触发 |
| 5 | 他现在没有回我的消息 | postedit 应命中 |
| 6 | 现在是星期二，他现在没有回我的消息 | postedit 误伤回归：两个子句都得在 |
| 7 | 对。我觉得可以。今天不行。 | 短子句保留（002e63a） |
| 8 | 大概10.5秒 | 小数点不被切（可选） |

### B3 结果

| 意图 # | 实识 src | tgt | 结论 |
|---|---|---|---|
| 1 你好 | 你好。 | Hello. | OK |
| 2 延迟 | 延迟大概有多少？ | About how much delay… | OK |
| 3 天气 | 今天天气怎么样？ | How's the weather today? | OK，未误拆 |
| 4 星期二 | **是星期二。** | Tuesday. | ASR 丢「现在」；单句 OK |
| （旁路） | 信期录。/ 星期五。 | Letter Books / Friday | 见上方「待确认」 |
| 5 回消息 | 他现在没有回我的消息。 | He hasn't replied to my messages yet. | **postedit canned 命中** |
| 6 复合 | **但是**星期二，他现在没有回我的消息。 | But Tuesday, he hasn't replied… | **两子句都在** |
| 7+8 | 见下「未触发」 | Yes, I think so. Not today. / About 10.5 seconds. | 小数 OK |

勾选：

- [x] 第 6 句译文里「星期二」还在（两子句都在）
- [~] 第 7 句「对。」单独念 — **未触发（条件不成立）**
- [~] 第 6 句 TTS 分两段 — **未触发（条件不成立）**
- [x] 无自激、无回声（日志无环回自激；听感请用户口头确认）
- [x] 没有重复念同一句

**未触发机制（设计如此，非失败）：**

1. **第 6 句不分两段播**  
   ASR 输出中间是 **`，`**（`但是星期二，他现在没有回我的消息`）。  
   A0 只认句末标点 `。？！?!` 与受保护的 `.`，**不认逗号**。  
   内容未丢；整句一播是预期行为。

2. **「对。」未单独念**  
   当场实际连说接近：  
   `对我觉得可以，今天不行，大概10.5秒`  
   （post 稿：`对我觉得可以，今天不行，大概10.5秒。`）  
   **无句号停顿** → 无 `对。` 子句边界 → 短子句保留 / A0 分播条件不成立。

---

## 提案（文档记录，未改代码）

| ID | 级别 | 现象 | 建议 |
|---|---|---|---|
| P3-dualsim-wav | **P3** | dualsim **不落**可用 `mic_raw` | **只改文档**：dualsim 后若要 `--post`，请 `--file` 指定原音频（或自备拷贝进 session）。不修 Recorder 除非以后要自动化。 |
| P2-session-meta | **P2** | 进程未走 `finally` 时缺 `meta.json`（本场 `131540`） | `--post` 在缺 meta 时用 wav 时长 + `git_commit` 回填，`mode` 标 `unknown` |
| P2-post-merge | P2 | 会后能量块并段（观察） | 与实时 A0 无关；要「会后句数≥live」再立项 |

---

## 收口

| 项 | 状态 |
|---|---|
| 核心回归（postedit / 小数 / 单句不误拆） | **PASS** |
| A0 实麦/回放（`20261106-130636` 并句 → sub_lines） | **PASS** |
| B 场两项「分播 / 对。单独念」 | **未触发（条件不成立）**，**不影响收口** |
| 待用户确认 | 「星期五」本人所说 vs TTS 串音 |

**本轮验收收口。** 提案未批准前不改代码。
