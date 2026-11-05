# 下一步：先把账算干净，再动空检

> 这份文件是评审意见 + 任务书。请先回答第二部分（只读，不改代码），再按第三部分执行。
> 不要跳过第二部分直接改默认——上一轮就是因为跳了，才有了现在这个混淆。

---

## 一句话

你报的 2×2 里，**(b) 臂捆了三个开关**（`REJECT_STALE` + `KEEP_TAIL` + `MISS_GROW_P90`），所以「默认已改为 b_only 画像：legacy + REJECT_STALE=True」这句话在 `app/config.py` 里实际对应 4 个 flag。
`ami_046` 的 `n=2 ok` 不是 (b) 赚的，是 `KEEP_TAIL` 把 utt1 末尾 0.8s 重转了一遍、恰好补回了 ASR 漏掉的 `ALL THIS DATA`。
在你把 23/48 当基线之前，先把这两个变量拆出来。

---

# 第一部分：我反驳你的四点

## 1. 你的 (b) 不是 (b)，是三个开关

`scripts/ab_loop_vs_stale.py:33-58` 的原文：

| cell | GUARD_MODE | KEEP_TAIL | REJECT_STALE | MISS_GROW_P90 |
|---|---|---|---|---|
| pre | legacy | **False** | False | **False** |
| a_only | soft | **True** | False | False |
| b_only | legacy | **True** | True | **True** |
| both | soft | True | True | True |

这不是 2×2，是「2×2 + 两个随臂漂移的共变量」：

- `a` 臂相对 `pre` 变了 **2 个**（soft + TAIL）
- `b` 臂相对 `pre` 变了 **3 个**（STALE + TAIL + P90）
- `b_only` 相对 `a_only` 同时变了 3 个（GUARD + STALE + P90）

而 `app/config.py:97-106` 现在的默认是四个全开：

```python
ASR_LOOP_GUARD_MODE = "legacy"
ASR_LOOP_STREAK = 2
ASR_LOOP_KEEP_TAIL = True        # ← 报告的表里没写
ASR_SPEC_REJECT_STALE = True
ASR_SPEC_MISS_GROW_USE_P90 = True  # ← 报告的表里没写
```

**后果**：任何人照你报告那句「legacy + REJECT_STALE=True」复现，会关掉 TAIL 和 P90，得到的不是 23。这就是我说的「画像」和「默认」不一致。

## 2. `ami_046` 的 ok 是 `KEEP_TAIL` 给的，不是 (b) 给的

四臂真实文本（`docs/ab_loop_vs_stale_*_detail.jsonl`）：

```
pre      n=2  wer=0.33  awkward
         utt1: So I I have you already calculated。
         utt2: Are you supposed to do that？

a_only   n=1  wer=0.67  wrong        ← soft 把两句并成一句（你查对了）

b_only   n=2  wer=0.28  ok
         utt1: So I I have you already calculated。
         utt2: We calculated all this data .。What are you supposed to do that？
                        ↑ 这半句是 utt1 末尾 0.8s 的二次转写

both     n=1  wer=0.67  wrong

ref      SO UM I UH HAVE YOU ALREADY CALCULATED ALL THIS DATA OR ARE YOU SUPPOSED TO DO THAT
```

看清楚这条链：

1. `pre` 里 utt1 的 ASR **输出**停在 `calculated`，所以 `ALL THIS DATA` 丢了 → WER 0.33
2. `b_only` 里 `KEEP_TAIL` 把 utt1 最后 0.8s 音频塞进 utt2 开头重转 → `ALL THIS DATA` 被捞回来 → WER 0.28
3. 但 utt1 的 audio **本来就包含**这 0.8s，ASR 也转过一遍（只是漏了字）→ 所以 `calculated` 出现了两次

**结论**：`KEEP_TAIL` 治的不是「切句丢音」，是「ASR 漏字」。它每次开火都以边界重复转写为代价，在 046 上刚好赚了。
所以 046 从 awkward→ok 与 (b) 无关（046 没有过期短稿问题），是 `KEEP_TAIL` 的一次幸运补偿。**如果按第一部分的建议关掉 TAIL，046 立刻回 awkward。**

## 3. 「23/48」里只有 1 条是干净归因

| 条 | 从 | 到 | 归因 | 干净吗 |
|---|---|---|---|---|
| `ami_008` | 0.89 wrong `Read through。` | 0.26 ok 完整长句 | STALE（四臂一致：pre/a_only 短稿，b_only/both 长句） | ✅ 干净 |
| `ami_046` | 0.33 awkward | 0.28 ok | **KEEP_TAIL**（见上） | ❌ 混入 TAIL |
| `ami_031 / 039` | WER 0.65 / 0.82 | 0.30 / 0.41 | STALE？P90？两者？ | ❌ 分不开 |

**所以「008/039 改善来自 (b)」这句话，008 成立，039 不能这么写**——039 在 a_only 里也是 0.82，在 b_only 里是 0.41，但 b_only 同时动了 STALE 和 P90 和 TAIL 三个。

## 4. 你要攻的 16 条空检，三个支点在代码里是空的

你在报告「为何没到 26/48」里写的是「本轮明确不修 VAD」，但更早的下一步计划是「只放宽开说、不放宽收句」。这条计划的三个支点我都实测过：

**(a) `VAD_SPEECH_RMS_MIN` 是空旋钮。**
地板降到 0.0，这 16 条**一段都切不出来**（endpoint 层直接喂，补 1s/2s/4s 尾静音，`segs` 全空）。
`docs/VAD_RMS_FLOOR_AB.md` 里那句「0.0 → 16/16 rescued」是文件模式的伪影——那个脚本结尾补 4s 静音再 `_force_hold_cut`，等于人工替 VAD 收句。

**(b) 「连续 K 帧 TEN=speech」这个信号不存在。**
`app/vad_stream.py:284`：

```python
if self._vad.is_speech_detected:      # ← 这是 method，不是 property
```

`bool(bound method)` 恒为 `True`，所以这个分支永远成立。拿它做 K 帧去抖，去抖对象是一个恒真的值，写出来是空操作。
顺带：下面那个 `elif self._speech_started:`（注释写「自行累计尾静音并强制 endpoint」）**整段是死代码，从未执行**。

**(c) 「只放宽开说」在代码里已经成立。**
`allow = self._speech_started or rms >= energy_floor`——一旦 `_speech_started`，音量门就完全放行。再写一遍「in_speech 之后回保守值」是 no-op。

**唯一有效旋钮是 `VAD_TEN_THRESHOLD`（`app/config.py:47`，当前 0.5）。**
我逐帧喂 256 样本窗实测（16 条空检文件）：

| `VAD_TEN_THRESHOLD` | 有任意一帧判语音的文件 | 命中帧数 |
|---|---|---|
| 0.5（现状） | **0/16** | 0 |
| 0.3 | **9/16** | 1313 |
| 0.2 | **15/16** | 2882 |
| 0.1 | 16/16 | 3647 |

这是**帧命中**，不等于 emit 出句子（还要过 `_min_speech` 和收句）。真正的 emit 要你用 harness 量，见第三部分 P1。
另外这 16 条整段喂 SenseVoice 的 WER 均值只有 **0.136**、16/16 ≤0.45——**模型听得清，是没送到它面前**。所以这一桶值得做，只是入口不是 RMS 门。

---

# 第二部分：先回答这 5 个问题（只读，不改代码，不改默认）

要求：每题给数字或原文，不要给判断。答不出来就写「答不出」——那本身就是信息。

**Q1.** `docs/ab_loop_vs_stale_b_only_detail.jsonl` 里 `ami_046` 的 `pipeline_src`，第二句的前 8 个词是什么？它和 `ref` 里的 `CALCULATED ALL THIS DATA` 重复了哪几个词？逐个列。

**Q2.** 从 `scripts/ab_loop_vs_stale.py:33-58` 抄出 `pre` 和 `b_only` 两臂的全部 flag 差异（不要从报告抄，报告漏了两个）。

**Q3.** 加一个臂 `stale_only`：`GUARD_MODE=legacy / KEEP_TAIL=False / REJECT_STALE=True / MISS_GROW_P90=False`。跑 AMI 48 条，回答：`pipeline_ok` 是多少？`ami_046` 几句、什么文本、什么判定？`ami_008` 什么文本？

**Q4.** 加一个臂 `tail_only`：`GUARD_MODE=legacy / KEEP_TAIL=True / REJECT_STALE=False / MISS_GROW_P90=False`。同样回答：ok 多少？`ami_046` 几句？`ami_008` 什么文本？

**Q5.** 用**当前默认配置**（不要改任何 flag）连跑两次 AMI，两次 `pipeline_ok` 各是多少？两次的 `ami_046` 是否一致？

---

# 第三部分：回答完之后，按这个走

## P0：拆混淆（必做，两次 ledger，约 16 分钟）

Q3/Q4 跑完，按结果查表：

| Q3 | Q4 | 说明 | 动作 |
|---|---|---|---|
| **22** | **22** | 两个开关各 +1，互不重叠（我预判这个） | `23 = STALE 1 条 + TAIL 1 条`。接着做 P0-b |
| 23 | 21 | 全归 STALE，TAIL 无收益 | 默认改 `legacy + TAIL=False + STALE=True + P90=False`，046 回 awkward（文本干净），报告里写明「046 是 ASR 漏字，不是切句」 |
| 21 | 23 | 全归 TAIL，008 的解释要撤回 | 先查 008 为什么在 STALE=False 下变好（理论上 TAIL 不影响它，若影响说明还有第三条路径），查清前不改默认 |
| 其它 | 其它 | 有交互 | 先扩 FLEURS 12 条复测，不要定默认 |

**P0-b（TAIL 的处置，这是我的立场）**

`KEEP_TAIL` 的机制是「把 utt1 已经转写过的最后 0.8s 再转一遍」。它按构造必然产生边界重复，只在 utt1 的 ASR 漏字时才有收益。
所以我建议：**默认关掉**，把 046 记为 ASR 漏字案例；若要保留，必须改成条件式——

```
只有当 utt1 的 ASR 输出词数 / 该段音频秒数 < 1.2 词/秒（明显偏短）时，才补尾 0.8s
```

判据阈值你先不要拍，先用现有 48 条统计 `word_count / audio_sec` 的分布，把 P10 当候选，附在报告里。**不要在没有分布的情况下写死一个数。**

## P1：TEN 阈值扫（并行，约 15 分钟）

复制 `scripts/ab_vad_rms_floor.py` → `scripts/ab_vad_ten_threshold.py`，**只换被扫的旋钮**：

```python
# 原来: for floor in FLOORS: config.VAD_SPEECH_RMS_MIN = floor
# 改成: for thr in THRESHOLDS: config.VAD_TEN_THRESHOLD = thr
THRESHOLDS = [0.5, 0.4, 0.3, 0.2]
```

其余一律不动，保留它已有的四列口径（`rescued` / `normal_ok` / `false_open_noise` / `synth_same`），这样数才能和 `docs/vad_rms_floor_ab.json` 并排看。

闸门（四条全过才算过）：

| 项 | 要求 |
|---|---|
| `rescued` | ≥ **8/16** |
| `normal_ok` | ≥ **24/25**（现基线 25/25） |
| `false_open_noise` | **0**（silence / white / keyboard） |
| `synth_same` | **3/3** |

**口径提醒（重要）**：这个 harness 的 `_pad()` 会补 0.5s 前导 + 1.0s 尾静音，结尾再 `_force_hold_cut` 强制 flush。所以 `rescued` 是**上限**，不是实麦表现。报告里必须写这一句，否则会被当成实机能力。

如果 0.3 过闸、0.4 不过，就取 0.3；如果 0.4 也过，取 0.4（更保守）。**不要为了多救一条往 0.2 以下走**——0.2 在 noise 上的行为还没测过。

## P2：只有 P1 过闸，才跑 ledger 配对

```cmd
.venv\Scripts\python.exe scripts\eval_ledger.py --corpus ami --skip-a5
```

按 id 逐条配对，报三件事，不要报总 ok 率：

1. 16 条空检里，几条 `n_utts>0`、几条转 `ok`
2. 原 23 条 ok 里，掉几条（**这条是硬闸门**）
3. `normal_ok` 的 25 条里，切点变化导致文本变了几条

闸门：转 ok ≥4 **且** 掉 ok ≤1。掉 2 条以上直接回滚，不看总分红。

## P3：FLEURS 12 条（每次改默认都跑）

```cmd
.venv\Scripts\python.exe scripts\eval_ledger.py --corpus fleurs --skip-a5
```

上一轮漏跑，补跑后 WER 0.242→0.251。**以后任何默认变更，AMI + FLEURS 必须成对出现**，不能只报 48 条。

---

# 第四部分：不要做

1. **不要再动 `VAD_SPEECH_RMS_MIN`**（空旋钮，实测 0.0 也切不出 16 条）
2. **不要写「连续 K 帧 TEN=speech」去抖**（`is_speech_detected` 是 method，条件恒真）
3. **不要把 `23/48` 写进结论**——它混了 STALE + TAIL + P90，且 Q5 还没量方差
4. **不要为了让 `ami_039` 过线去改 `content_density`**。它是判据假阴性（tgt「对我来说,这没什么区别」译对了，参考文本里 6 个语气词把分母撑大），记 1/48 已知损失即可。我试过两种改法：分母换 `src` 会让 3 条 WER>0.45 的句子密度过线；剔语气词要精确剔掉 `any`/`know` 才刚好过线——那是拿尺子对样本
5. **不要在这轮碰**：MT / postedit / TM / `ASR_2PASS_MERGE_MODE` / weekday 白名单 / 任何词表白名单
6. **不要把 `_force_hold_cut` 的 flush 结果当成实麦能力**（见 P1 口径提醒）

---

# 第五部分：交付物

| 文件 | 内容 |
|---|---|
| `docs/ASR_LOOP_CUT_AB.md` | 追加一节「去混淆」：Q2 的 flag 对照 + Q3/Q4 结果 + 046 归因更正（KEEP_TAIL，不是 (b)） |
| `docs/ab_loop_vs_stale.json` | 保留原四臂；新臂写 `docs/ab_loop_vs_stale_6arm.json`，**不覆盖旧文件** |
| `docs/VAD_TEN_THRESHOLD_AB.md` + `.json` | P1 的四列结果、每档的 `rescue_ids`、以及「已过/未过哪条闸门」 |
| 一行结论 | 23 归谁（STALE / TAIL / 各一条），以及默认最终是哪四个 flag |

报告开头必须有一行「本次默认 = GUARD_MODE=? / KEEP_TAIL=? / REJECT_STALE=? / P90=?」，四个都要写，不许省略。

---

# 第六部分：复现命令（CMD）

```cmd
cd /d D:\xuanjian\realtime-translate

REM Q3/Q4：加两个臂后重跑（改 scripts\ab_loop_vs_stale.py 的 CELLS）
.venv\Scripts\python.exe scripts\ab_loop_vs_stale.py

REM 046 的文本（Q1，只看，不改）
.venv\Scripts\python.exe -c "import json;[print(r['id'],r['n_utts'],r['pipeline_src']) for r in (json.loads(l) for l in open('docs/ab_loop_vs_stale_b_only_detail.jsonl',encoding='utf-8') if l.strip()) if r['id'] in ('ami_046','ami_008')]"

REM P1：阈值扫（复制脚本后改旋钮）
.venv\Scripts\python.exe scripts\ab_vad_ten_threshold.py

REM P3：FLEURS
.venv\Scripts\python.exe scripts\eval_ledger.py --corpus fleurs --skip-a5

REM 单测
.venv\Scripts\python.exe -m pytest tests\ -q
```

---

# 附：如果你认为我错了

只接受用下面三种证据反驳，其它一律算猜测：

1. 贴出 `scripts/ab_loop_vs_stale.py` 里 `pre` 与 `b_only` 的 flag 完全相同的证据（那说明我看错文件了）
2. 贴出 `ami_046` 在 `stale_only` 臂里的 `pipeline_src`，证明它不依赖 TAIL 也能拿到 `ALL THIS DATA`
3. 贴出 `VAD_TEN_THRESHOLD=0.5` 下这 16 条有 emit 的日志（那说明我的帧命中探针测错了）

否则按第三部分执行。
