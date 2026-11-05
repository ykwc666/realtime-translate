# 征求意见：postedit 锚定修复 + 会后重译闸门修订（2026-11-05）

请你作为**独立评审**，只根据下文数据与约束给意见。回答请短、可执行：修得够不够 / 还漏什么 / 下一步不要做什么。

---

## 1. 背景（一句话）

会后重译链路已通（`record → SenseVoice 分块 → MT → 双语稿`），但发现 **MT postedit 用子串命中整句替换 canned，会静默删掉复合句后半段**；且原先「分块闸门」因语料全 &lt;25s 而**恒真未测**。本轮只修 postedit 锚定 + 闸门，不调 VAD/块长。

工作区：`D:\xuanjian\realtime-translate`  
相关提交：`3e1ea53` `fix(mt): anchor postedit rules; gate multi-chunk retranscribe`

---

## 2. 红线（本轮遵守）

- 未改 `app/config.py` 任何 VAD/ASR 默认值，未动 `dual_track` 切句
- 未改默认块长 25s
- 未换 ASR/MT 模型；未扩 TM
- ledger 数字允许因 postedit 松手而变；**禁止为保 27/48 而保留删内容规则**

---

## 3. P0 bug：怎么复现、怎么修

### 3.1 现场

`Translator.translate()` 里 `postedit_zh_en(text, raw)` **实时与会后共用**（`app/translate.py`）。

| 输入（src） | 修前 postedit 输出 | 丢掉了 |
|---|---|---|
| 现在是星期二，他现在没有回我的消息。 | `He hasn't replied to my messages yet.` | 「现在是星期二」 |
| 我觉得听不清，你能再说一遍吗？ | `I can't hear clearly.` | 「你能再说一遍吗」 |
| 今天天气怎么样？我下午想出去走走。 | `How's the weather today?` | 「我下午想出去走走」 |

MT raw 往往是对的；被 postedit **整句替换**掉。

### 3.2 根因

`app/mt_postedit.py` 里 canned 规则用 `re.search` 子串命中 → `return "固定英文句"`，丢弃 `hyp`。

### 3.3 改法（已落地）

对 `postedit_zh_en` / `postedit_en_zh`：

1. **canned → `re.fullmatch`**（整句才替换）
2. **子句分隔闸**：源句含分隔符则跳过全部 canned，直接返回 `hyp`  
   - 中文：`， , ； ;`  
   - 英文：分号，或 `, and|but|so|then|while|which`（**不含** `No, …` / `Hang on, …` 称呼逗号，否则英文 canned 几乎全废）
3. **长度闸**：canned 词数 &lt; hyp 词数 50% → 退回 `hyp`

词级 scrub（如「延缓→延迟」）仍保留，不是整句 canned。

### 3.4 测试

- `tests/test_postedit_anchor.py`：表驱动正反例 + ledger 不变量冒烟
- 原有 `tests/test_mt_postedit.py` 全绿
- `tests/test_retranscribe.py`：`split_chunks` / `split_sentences` / `_alloc_timestamps`
- 合计相关：**25 passed**

---

## 4. 闸门修订（会后重译）

语料事实：`ledger_corpus` n=60，p50≈3.9s，max≈11.5s，**无 &gt;25s** → 旧 G2「Δ≤0.02」在单文件上恒走单块，Δ=0.0 是假绿。

| 闸门 | 判据 | 本轮机结果 |
|---|---|---|
| G1 | empty=0 且 char_ratio&lt;0.85 条数 ≤2；低比条目打印 ref/hyp/ratio | empty=0，低比=2，`g1_ok` |
| G2_single_chunk | 单块 WER ≤ oneshot+0.02；报告注明「只对单块成立」 | Δ=0.0，ok |
| G6 多块 | 前 30 条 +0.6s 间隔拼 ~130s；chunks≥5；WER ≤ oneshot_30+**0.06**；打印边界秒数与 ±100ms RMS | 129s，**chunks=6**，Δ=**+0.0408**≤0.06；边界 RMS≈0 |
| live_compare | 仅 `meta.mode∈{vad,duplex}` 且同 session `live.jsonl` 非空；否则 `null`；**不读** `data/harvest/` | 已改 |

脚本：`scripts/gate_offline_retranscribe.py`  
说明：`docs/OFFLINE_RETRANSCRIBE.md`「闸门修订」节

---

## 5. ledger（修后复测）

`eval_ledger.py --skip-a5 --corpus ami,fleurs`：

| 集 | pipeline ok | WER |
|---|---|---|
| AMI | **27/48** | **0.4433** |
| FLEURS | **11/12** | **0.1352** |

与修前 TEN 0.4 checkpoint **一致**（本轮 postedit 主要伤中文复合句；AMI 英会议主路径几乎不动）。

---

## 6. 实麦端到端（mode: vad，非 file 灌录）

```text
python run.py --mode vad --no-tts
→ data/sessions/20261105-163258/
python run.py --post data\sessions\20261105-163258
```

会后 `transcript.md`（定稿，已滤静音幻觉）：

| 原文 | 译文 |
|---|---|
| 你好，延迟大概有多少？ | Hello. How much delay is it? |
| 今天天气怎么样？ | How's the weather today? |
| 现在是星期二？ | It's Tuesday. |
| …支付链路的审批会，大概2个小时。 | …payment chain… two hours. |
| 我们需要把这个方案再对一遍，然后发给产品。 | We need to repeat this programme… |
| 帮我把这个文件发给他，再确认一下时间。 | Send him this document… |

要点：直播里「天气+星期二」曾并成一句；会后按标点拆成两句且**两句都在**——不再只剩天气、丢掉星期二。  
`live_compare`：`live_missed=0`（`meta.mode=vad`）。

顺带：会后对 ASR 纯标点 / &lt;2 字符内容丢弃，避免 SenseVoice 在长静音块上幻觉 `.` / `？`。

---

## 7. 已知局限（请评审是否接受）

1. **英文子句闸**比中文松（保留称呼逗号）——有意为之；若你认为英文也应对所有 `,` 禁用 canned，请给可过现有 `test_mt_postedit` 的改法。
2. **G6 是拼接语料**，边界不自然；15s vs 25s 仍不足以下结论改默认块长（任务书禁止本轮改块长）。
3. 实麦 session 前半有长静音（开麦后未立刻说话），会后靠空内容过滤，不是 VAD 问题。
4. canned 规则本身仍是通话域启发式；锚定只保证「不误删复合句」，不保证 canned 译文质量。

---

## 8. 请你回答

1. **P0 三道闸是否足够？** 还是必须对英文也禁用所有 `,`？
2. **G6 容差 +0.06** 是否合理？还是应强制默认块改 15s（有 Δ=+0.003 的拼接证据，但边界不自然）？
3. 实麦定稿里「天气 / 星期二」拆成两句——你认为算修复成功，还是还要保证「并句时 MT 整句保留」的单测？
4. **下一步主方向**（选一）：A 双语字幕 UI / B 修 `silence_forced` 死分支 / C 会后说话人/摘要 / D 停功能继续打磨稳定性  
5. **明确列出：下一步不要动的旋钮**

输出格式建议：

```
结论：...
主风险：...
主方向：...
不要做：...
争议点：...
```
