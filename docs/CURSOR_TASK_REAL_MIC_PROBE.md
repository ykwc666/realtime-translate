# Cursor 任务：实麦取证（只埋点，不动阈值）

## 0. 一句话目标

把「实麦跑一遍，凭感觉说行不行」变成「实麦跑一遍，拿到 6 个数」。
**本轮唯一允许的代码改动是"记录"，不是"调整"。**

---

## 1. 红线（违反即回退）

以下任何一项出现在 `git diff app/` 里，本轮作废：

- `VAD_TEN_THRESHOLD`（当前 0.4）
- `VAD_SILENCE_SEC`（0.45）
- `VAD_SPEECH_RMS_MIN`（0.035）
- `VAD_WEAK_GAIN_MAX`（0.0）
- `ASR_LOOP_GUARD_MODE` / `ASR_LOOP_KEEP_TAIL` / `ASR_SPEC_REJECT_STALE` / `ASR_SPEC_MISS_GROW_USE_P90`
- 任何 `prefer_2pass` / 合并策略 / 仲裁阈值

**也不许跑新的 A/B 扫描。** 0.4 已在两套语料上站住（AMI 27/48、FLEURS 11/12、0 掉级），
在 48 条样本上再扫一档就是拟合噪声。

---

## 2. P0：先做 checkpoint（5 分钟，防丢）

工作区有 136 项未提交改动，其中包含这次的 0.4 和全部 A/B 证据。先落盘。

```cmd
cd /d D:\xuanjian\realtime-translate
echo data/ledger_corpus/>> .gitignore
git add -A
git commit -m "checkpoint: TEN VAD 0.5->0.4 (AMI ok 22->27, FLEURS 9->11, 0 regressions) + ledger eval harness"
```

说明：`data/ledger_corpus/` 由 `scripts/build_ledger_corpus.py` 生成（12MB wav），不进库；
提交后 `git status` 应为 clean。

验收：`git show --stat HEAD` 里能看到 `app/config.py`，且 `grep VAD_TEN_THRESHOLD app/config.py` 仍是 `0.4`。

---

## 3. P1：埋点（行为零变化）

### 3.1 要记录的字段（每句一条）

| 字段 | 含义 | 取值来源 |
|---|---|---|
| `utt_idx` | 第几句 | 自增 |
| `audio_sec` | 送进 ASR 的音频长度 | 已有 |
| `open_peak` / `open_rms` | **开说那一窗**的原始峰值 / RMS（未加权） | `VadEndpoint.feed()` 里 `self._speech_started` 由 False→True 的那一刻 |
| `open_prob` | 开说那一窗 TEN 的语音概率 | 若 API 拿不到就写 `null`，**不要猜** |
| `emit_path` | 哪条路径吐的段：`model_endpoint`（`self._vad.empty()` pop）/ `silence_forced`（`self._silence_samples >= self._silence_needed`）/ `flush` | 三个 emit 点各打一个标记 |
| `cut_reason` | 收句原因 | 仲裁：透传 `dec.reason`；VAD：`vad_endpoint`；`max_utt`；`asr_loop`；`hold_timeout`；`flush`；`empty_abort` |
| `trailing_silence_sec` | 切句时报告的尾静音 | `endpoint.trailing_silence_sec` |
| `n_partials` | 这句出过几次 partial | 已有计数则复用 |
| `src` / `tgt` | 识别 / 译文 | 已有 |

### 3.2 两个"顺带就能证伪"的问题（只记录，不修）

请在埋点里同时输出这两个计数器，本轮**不要修**：

1. `emit_silence_forced_count`：`app/vad_stream.py:293` 的 `elif self._speech_started:` 分支
   依赖 `self._vad.is_speech_detected` 这个 **method** 被当 bool 用（`sherpa_onnx.VoiceActivityDetector.is_speech_detected` 是 instancemethod，恒真）。
   如果整个会话 `emit_silence_forced_count == 0`，就证明该分支是死代码。
2. `trailing_silence_always_zero`：如果每句 `trailing_silence_sec` 都是 `0.0`，同理证明
   `_silence_samples` 从不累加（因为累加只在那个死分支里）。

**这两个计数器是只读证据，不是修复。**

### 3.3 退出汇总（Ctrl+C 或流结束必须打印）

```
=== 实麦自检 ===
utts=8  empty_abort=1  emit_model=8  emit_silence_forced=0  flush=0
open_peak  p10=0.021 p50=0.048 p90=0.093
open_rms   p10=0.006 p50=0.014 p90=0.031
cut_reason vad_endpoint=6  max_utt=1  asr_loop=1
trailing_silence 全为 0: yes
--- 逐句 ---
#1 1.8s peak=0.052 rms=0.015 path=model_endpoint reason=vad_endpoint src='你好。' tgt='Hello.'
```

同时落盘一份 `data/probe/<YYYY-MM-DD-HHMM>.jsonl`（逐句一行）+ 同目录 `summary.json`。

**不要改 `app/harvest.py` 的契约**（`docs` 里那份字段表是给别人消费的），probe 走独立文件。

---

## 4. 闸门（写完先自己跑一遍，必须全绿）

> 上一轮的教训：`normal_ok >= 24` 而集子只有 22 条，闸门恒假 → 把真实提升判成失败。
> **本轮每个闸门都必须先用"当前代码"验证能过，过不了说明闸门写错了，先修闸门。**

| 闸门 | 判据 | 怎么验 |
|---|---|---|
| G0 基线不变 | `python -m pytest -q` 数字与改动前**完全一致** | 改前先跑一次记数字，改后再跑 |
| G1 离线结果不变 | `python scripts/eval_ledger.py --skip-a5` → AMI `27/48`、WER `0.4433`；FLEURS `11/12`、WER `0.1352` | 逐位比对，允许 ±0 |
| G2 红线未破 | `git diff app/config.py` 为空 | `git diff --stat app/config.py` |
| G3 汇总可打印 | 无麦时用 `--mode file` 跑一个 wav，退出能打出上面的汇总块 | 用 `cache/ab_weekday/accept_phrases.wav` |
| G4 不阻塞 | 埋点后 `file` 模式总耗时增幅 < 3% | 同一文件跑两次对比 |

G1 是硬闸门：**埋点如果改变了任何一句的切分，说明写成了行为改动，回退重写。**

---

## 5. 交付物

1. 提交：`probe: live-path cut attribution (no behavior change)`
2. `docs/REAL_MIC_PROBE.md`：字段表 + 汇总样例 + G0~G4 实测数字
3. 一段话回答：`emit_silence_forced_count` 是不是 0？`trailing_silence_sec` 是不是恒 0？
   （是/不是 + 证据行号即可，**不要顺手修**）

---

## 6. 不归 Cursor 的部分（用户来做）

埋点合并后，用户戴耳机跑：

```cmd
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe run.py --mode vad
```

说这 5 句（说完每句停 2 秒）：

1. `你好，延迟大概有多少？`
2. `今天天气怎么样？现在是星期二。`（连着说，中间不喘气）
3. `我明天上午十点要去客户那边开一个关于支付链路的评审会，大概两个小时。`（长句，约 10 秒）
4. `我们需要把这个方案再对一遍，`（停 1.5 秒，不说话）`然后发给产品。`（中途停顿，**不该**被切成两句）
5. `帮我把这个文件发给他。`（说完敲两下键盘）`再确认一下时间。`（键盘声**不该**开说）

判读表：

| 现象 | 结论 | 下一步 |
|---|---|---|
| `utts=0` 或 `open_peak` 多数 < 0.035 | 真实麦克风也是弱电平，语料没骗人 | 下一轮做 `VAD_SPEECH_RMS_MIN` + 弱增益，单变量 |
| 第 4 句被切成两句 | 0.45s 静音对自然停顿太短 | 单变量试 0.6 |
| 第 5 句键盘声开说 ≥2 次 | 0.4 太灵敏 | 加"连续 2 窗确认"或短 hangover，不要退回 0.5 |
| 第 3 句被 15s 强切 / 复读强切 | 长句路径问题 | 记 `cut_reason` 分布，单独查 |
| 5 句都正常、`emit_silence_forced=0` | 实麦可用；死分支是纯技术债 | 停止调 VAD，转交付（README / 双语字幕 / 会后重译） |
