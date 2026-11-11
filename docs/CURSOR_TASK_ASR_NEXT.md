# 提示词：zh→en ASR 这一刀怎么打（先别调参）

## 0. 背景：我独立核了一遍，结构和你想的不一样

`data/eval/dialogue_mslt/detail.jsonl` 里 30 条 zh_en，我按句长拆开：

| 分组 | n | asr_ok | 可用率 |
|---|---:|---:|---:|
| 短句 ≤5 字 | 10 | 3 | **30%** |
| 长句 >5 字 | 20 | 16 | **80%** |

11 条"听错"的真实构成：

- **5 条是同义互换，产品上可播**：`006/013/015/026` 对→嗯，`010` 好→嗯，没错
- **2 条短句听成别的话**：`020` 我想→哦，了解；`027` 我也很喜欢→也去一下吧
- **4 条整句全错**（就是你说的火烧香蕉那类）：`003` 你知道这个吗→就是这意思啊；`016` 不知怎么会挑起话题啊→不知道怎么回，调解话题呀；`022` 它叫火烧香蕉→更新坏代会比较快；`028` 宋冬野的，大陆歌手唱的→我觉得现在好像小孩压力还蛮大的

我核对过音频时长与参考字数（2.4s↔7字、4.9s↔12字），**音频与文本对齐是对的，是识别真的错了，不是切分错位。**

所以：**真实 ASR 可用 ≈ 80%，不是 63%。** 63% 里有 5 个点是词面判定造成的假失分。整句全错只有 4 条，不是最大的一类。

---

## 1. 先做 holdout，否则后面全是自欺

**现状**：60 条全部来自 MSLT **Test** 集（manifest `notes` 字段写着 `MSLT_Test_CH_xxxx`），而且这 60 条已经被用来调参（TM 6 条 + postedit 若干规则）。现在这个 70%，是在自己改过的考卷上考的。

**要做**

1. 给 `scripts/import_mslt_dialogue.py` 加 `--out` 参数，**默认值必须保持 `data/dialogue_corpus` 不变**。
2. ⚠️ **先加保护再动手**：现有代码是

   ```python
   if RAW.exists():
       shutil.rmtree(RAW)
   ```

   不加 `--out` 就跑 Dev 会**把现有 60 条 KPI 语料直接删掉**。请加：目标目录已有 `manifest.jsonl` 且未传 `--force` 时退出，不删任何东西。
3. 导出 Dev 集到独立目录：

   ```bat
   .venv\Scripts\python.exe scripts\import_mslt_dialogue.py --zip data\dialogue_corpus\MSLT_Corpus.zip --out data\dialogue_corpus_dev --prefer-split Dev --seed 11 --n-zh-en 30 --n-en-zh 30
   ```

4. 给 `scripts/eval_dialogue_mslt.py` 加 `--manifest` 和 `--out-dir` 参数（默认值不变，保持现有行为）。
5. 用 Dev 集跑一遍，**不许用 Dev 的结果去调参**。

**交什么**

Dev 集 zh_en 的：`asr_ok_rate` / `mt_ok_rate` / `mt_ok_given_asr_rate` / `empty_asr_rate`。

**怎么判**

- Dev 上 `mt_ok ≥ 60%` → 70% 大体是真的，可以继续往下走
- Dev 上 `mt_ok < 50%` → Test 的 70% 主要是补丁撑的，**停止优化，先做泛化**

---

## 2. 给 detail 补 online / offline 双假设（只加字段，不改逻辑）

现在 `detail.jsonl` 只有最终 `asr_hyp`。你要判断"该不该加强 offline 权重"，**现在没有数据可依**——不知道这 4 条里 online 和 SenseVoice 各自听成了什么。

在 `run_one()` 里补三个字段：

```
asr_online    : 流式 Paraformer 的假设
asr_offline   : SenseVoice 2pass 的假设（没跑就 null）
arbiter_pick  : 最终采纳了哪一路（online / offline / merge）
```

**只加字段，不改仲裁逻辑，不改 `ASR_SPEC_*` 任何值。** 加完重跑现有 Test 集，交 `003 / 016 / 022 / 028` 这 4 条的三路对照。

**怎么判**

- 4 条里 ≥2 条是「offline 听对、online 听错」→ 加强 offline 有权重可调，可以进入下一步
- 两路都错 → 模型能力上限，调权重没用。**这一刀到此为止**，别硬调

---

## 3. 短回应：这是口径问题，不许自己改 KPI

7/11 的失分来自短句，其中 5 条是「对/好 ↔ 嗯」同义互换。

- **不许**自己把判定改成「嗯/对都算对」。那是改考卷，必须单独提、单独批。
- 允许做的是**诊断**：这 7 条在 ASR 阶段发生了什么——是 online 提前收句、还是短句被 `MIN_FINAL_ASR_SHORT_OK` 之外的逻辑处理掉了。
- 先出诊断，不要改行为。

---

## 4. 碎切：先加度量，再谈改

`multi_utt_rate = 0.2` 只说明"一条 wav 被切成多段"，**不说明坏**。切开但每段都译对，产品上没损失。

先加两个度量，跑一轮看基线：

- `fragment_lost`：某段覆盖率 < 0.8（后半句丢了）
- `fragment_dup`：相邻两段内容重复

现状参考：我实测 `asr_coverage < 0.8` 是 **0 条**，暂时没有「后半句丢」。加完度量再决定要不要动 VAD 之外的切分逻辑。

---

## 红线（碰了直接退回）

- `VAD_TEN_THRESHOLD = 0.4` / `VAD_SILENCE_SEC = 0.45` / `VAD_SPEECH_RMS_MIN = 0.035`
- `MIC_DENOISE` 保持 `False`
- KPI 判定口径（`mt_usable` 的阈值、`asr_ok` 的 0.35）不许改
- 不许拿 AMI 当 KPI
- 不许为了 Dev 集的分数去改 Test 集上的规则

---

## 交数格式（沿用）

```
n: 有声 X / 总行 Y
zh_en  asr_ok_rate / mt_ok_rate / mt_ok_given_asr_rate / empty_asr_rate
Dev 集同上（单独一段，不许和 Test 平均）
最坏项: 哪个指标变差、多少
复现命令: 一行
```

---

## 一句话

**先把尺子校准（holdout），再弄清 4 条长句到底是哪一路听错的（双假设），然后才谈调参。** 顺序反了，调出来的权重只是对 Test 集过拟合。
