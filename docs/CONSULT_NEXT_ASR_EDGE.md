# 征求意见：realtime-translate 下一步 ASR/VAD 该怎么走

请你作为**独立评审**，只根据下文数据与约束给意见；不要假设还能改 MT 规则或换大模型。  
回答请短、可执行：选 1 个主方向 + 1 个备选 + 明确「不要做什么」。

---

## 1. 项目是什么

本地实时语音翻译管线（Windows）：

`采集 → TEN/Silero VAD → 流式 Paraformer（online）+ SenseVoice（offline 二遍）→ prefer_2pass 合并 → CT2 MT → TTS`

验收主集：AMI（英会议短句，48 条）+ FLEURS（12 条），**紧闸门 model-judge**。  
对照：`pipeline`（真链路 ASR→MT）vs `mt_only`（官方 ref 直接 MT）。

工作区：`D:\xuanjian\realtime-translate`（未要求 commit）。

---

## 2. 硬约束（违反即废案）

1. **不换 ASR 模型**（不换 Paraformer / SenseVoice / 更大 Whisper 等）。
2. **不扩 `mt_postedit` / TM / 通话域启发式**（已判定在公开英会议上收益≈0，防过拟合）。
3. **不回头改双轨合并默认**：`ASR_2PASS_MERGE_MODE = "offline"` 已用 oneshot + 流水线复测立住，保留。
4. 改动必须能 **A/B**：噪声 false_open、合成 `cache/ab_weekday/*`、ledger AMI/FLEURS pipeline ok。  
   任一明显回退 → 回滚。
5. 优先**机制**，少加「星期」类白名单（weekday 保护已加一条，够用）。

---

## 3. 当前数字（有效默认）

| 指标 | 值 |
|------|-----|
| AMI pipeline ok | **20/48 = 41.7%** |
| FLEURS pipeline ok | **9/12 = 75%** |
| AMI / FLEURS mt_only ok | **93.8% / 91.7%** |
| 差距含义 | 锅在 **ASR/VAD/切句**，不是 MT |

配置要点：`VAD_BACKEND=ten`，`VAD_SPEECH_RMS_MIN=0.035`，`ASR_2PASS_MERGE_MODE=offline`，`VAD_WEAK_GAIN_MAX=0`（关）。

失败结构（offline 合并后、gain=0）：

- ASR-gap（mt_only=ok 且 pipeline≠ok）≈ **29/31** 非 ok
- 其中 **`n_utts=0` 空检 ≈ 15–16 条**（最大桶）
- 其余：半句截断、碎片识别、语种串扰等
- ledger 英会议集上几乎无「星期」句；weekday 问题主要在中文合成/实麦

---

## 4. 已做成的（可视为基线）

1. **2pass 默认信 offline**：oneshot AMI WER 0.200→0.157；流水线 AMI 37.5%→41.7%，FLEURS 66.7%→75%。
2. **weekday 保护**：online 已是完整「星期X」且 offline 不是 → 信 online（挡「星期三」→「星期啊」）。
3. TEN-VAD 替 Silero：弱信号 A/B 曾救 6/20 `vad_never_open`，false_open=0（相对降 Silero 阈值更稳）。

---

## 5. 已否决的死路（不要再推同样的）

### A. 直接降低 `VAD_SPEECH_RMS_MIN`

- 空检窗 RMS 极低（多数 p95≪0.035）；TEN 在 `rms_min=0` 时 16/16 能开。
- 但地板降到 0.02/0.01：**keyboard 噪声 false_open≥1**；再低连 white/silence 也开。
- 结论：**不能靠绝对地板换召回**。证据：`docs/VAD_RMS_FLOOR_AB.md`。

### B. 弱峰 VAD 增益 `VAD_WEAK_GAIN_MAX=3`（峰&lt;0.03 才放大送入 VAD）

- 噪声门过：rescued 8/16，false_open=0；合成仅「你好」被拆成独立句。
- **ledger 紧闸门惨败**：AMI 41.7%→**25%**；空检 16→8，但正常句切碎（翻到 ok +7，**掉出 ok −13**）。
- 结论：**增益已实现并回滚默认=0**；「只放大弱窗」仍会改变切点分布。证据：`docs/VAD_WEAK_GAIN_AB.md`。

### C. `VAD_MIN_UTT_SEC` 合并碎句

- 基线上 short_lt_1s 已是 0；切点多为 `forced_trunc`，旋钮空转。不作为下一步。

### D. 继续堆 MT postedit / 换合并回 legacy

- mt_only 已 ~94%；legacy 合并更差。禁止。

---

## 6. 我方（当前执行者）的倾向性想法

按优先级：

### 方向 1（首选试探）：**「打开」与「切句」解耦的弱信号门控**

问题本质不是「听不见」，而是：

- 开说门槛（RMS）与  
- 收句/强切（unchanged、silence、forced_trunc）

被绑在同一套敏度上。增益提高召回的同时提前切碎好句。

想试的机制（仍不换模型）：

- **仅放宽「首次开说」**，且要求 **连续 K 帧** TEN=speech 且弱峰（抑制 keyboard 单峰）；
- **一旦 `in_speech`，RMS/增益策略回到现行保守值**，避免句中更碎；
- 或：弱信号只影响 VAD `accept_waveform` 的开说，**不降低** `VAD_FORCE_END_UNCHANGED*` / silence 收句。

验收：空检 rescued↑，AMI 已 ok 子集掉出 ≤2，keyboard false_open=0，合成文本不回归。

### 方向 2：**专打半句 / forced_trunc**

对 pipeline 有字但 WER 高、以功能词结尾的句（如 ami_031 `…you can only do`）：

- 分析强切原因（unchanged complete vs weekday vs max_utt）；
- A/B：完整句略增 wait，或 forced_trunc 时短尾并入下一句（此前笔记提过，尚未做）。

预期：不救 `n_utts=0`，但可能抬一部分「有检出却 wrong」。

### 方向 3（低优先）：**空检单独「文件级/句级」后处理试探**

例如极短 pad、或仅对从未 open 的 buffer 做一次 offline oneshot（破坏「流式纯正性」，仅作归因上限，不当默认）。

用于回答：「若 VAD 完美，SenseVoice 在这些 AMI 空检上 WER 能到多少？」— 定天花板，再决定是否值得抠 VAD。

### 不建议现在做

- 换大 ASR / 云端 API  
- 再调 `prefer_2pass` 主策略  
- 再开弱增益当默认（除非加上「只影响开说、不影响收句」的硬隔离并过 ledger）  
- 扩中文星期白名单  

---

## 7. 请你回答的问题

1. **你选方向 1 / 2 / 3 还是别的？** 一句话理由。  
2. 若选弱信号：请给出**具体可写进配置的门控伪代码**（含防 keyboard 条件），以及**必须过的 A/B 闸门**。  
3. 你认为 AMI 41.7%→目标多少算「这轮值做」？若你认为本地 Paraformer+SenseVoice 在 AMI 远场上天花板就在 ~45–55%，请直说，并建议是否该换验收集（更贴近产品麦距）而不是继续刷 AMI。  
4. **明确列出：下一步不要动的旋钮**（防止过拟合）。  
5. 若你与上文「方向 1」冲突，指出**哪条证据被误读**。

---

## 8. 附录路径（需要细节时）

- `docs/ASR_2PASS_MERGE_AB.md` — offline 合并  
- `docs/VAD_RMS_FLOOR_AB.md` / `docs/vad_rms_floor_ab.json` — RMS 地板  
- `docs/VAD_WEAK_GAIN_AB.md` / `docs/vad_weak_gain_ab.json` — 弱增益 + ledger 回滚  
- `docs/vad_weak_signal_ab.json` — TEN vs Silero vs gain  
- `docs/ledger_eval_summary_after_offline_merge.json` — 当前有效分账  
- `docs/EMPTY_UTT_DECOMPOSE_REPORT.md` / `docs/ledger_review_15_notes.md` — 空句与抽查  

输出格式建议：

```
结论：...
主方向：...
备选：...
不要做：...
闸门：...
争议点：...
```
