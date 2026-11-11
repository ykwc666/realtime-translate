# ASR 下一刀诊断（holdout + 双假设）— 2026-11-11

按 `docs/CURSOR_TASK_ASR_NEXT.md`：**只校准与诊断，不调 VAD / ASR 权重 / KPI 口径。**

## 交数

```
n: 有声 60 / 总行 60（Test）；Dev 另 60

Test zh_en  asr_ok_rate=0.6333  mt_ok_rate=0.7000  mt_ok_given_asr_rate=1.0000  empty_asr_rate=0.0000
Dev  zh_en  asr_ok_rate=0.4000  mt_ok_rate=0.4667  mt_ok_given_asr_rate=1.0000  empty_asr_rate=0.0000

最坏项: Dev asr_ok / mt_ok 相对 Test 掉约 23pp / 23pp（MT given-ASR 仍 100%）
复现:
  .venv\Scripts\python.exe scripts\import_mslt_dialogue.py --zip data\dialogue_corpus\MSLT_Corpus.zip --out data\dialogue_corpus_dev --prefer-split Dev --seed 11
  .venv\Scripts\python.exe scripts\eval_dialogue_mslt.py --manifest data\dialogue_corpus_dev\manifest.jsonl --out-dir data\eval\dialogue_mslt_dev
  .venv\Scripts\python.exe scripts\eval_dialogue_mslt.py
```

## 1. Holdout 闸门

| 集 | zh_en mt_ok | 判定 |
|---|---:|---|
| Test（调过 TM/postedit 的考卷） | **70%** | — |
| Dev（未调参） | **46.7%** | **&lt;50% → 停止用 Test 分数硬调 ASR/规则** |

补充：两侧 **`mt_ok_given_asr_rate = 100%`**。译文补丁在「听对」时泛化；Dev 掉分主因是 **ASR 可用 40%**（Test 63%），不是 MT 过拟合 alone。

默认目录有 `manifest.jsonl` 时 import **拒绝覆盖**（需 `--force`）；Dev 写入 `data/dialogue_corpus_dev/`（gitignore）。

## 2. 长句双假设（Test `003/016/022/028`）

| id | ref | final hyp | online | offline | pick | 结论 |
|---|---|---|---|---|---|---|
| 003 | 你知道这个吗？ | 就是这意思啊。 | 就是这意思啊 | 就是这意思啊。 | online | **两路都错** |
| 016 | 不知怎么会挑起话题啊。 | 不知道怎么回，调解话题呀。 | 不知怎么回调解话题呀 | 不知道怎么回，调解话题呀。 | merge | **两路都错** |
| 022 | 它叫火烧香蕉。 | 更新坏代会比较快。 | 更新坏蛋会比较快 | 更新坏代会比较快。 | merge | **两路都错** |
| 028 | 宋冬野的。大陆歌手唱的。 | 我觉得现在好像小孩压力还蛮大的。 | …小孩儿力还很大大 | …小孩压力还蛮大的。 | merge | **两路都错** |

**offline_helps = 0 / 4。** 按任务书：两路都错 → **不上 offline 权重**；本刀到此为止。

## 3. 短回应诊断（只诊断，不改 KPI）

| id | ref | hyp | cut_reason | 说明 |
|---|---|---|---|---|
| 006 | 对。 | 嗯。 | offline_rescue | 空 partial → 离线救回；字面 CER 仍错 |
| 013 | 对。 | 嗯。 | offline_rescue | offline 见 `いん` → 假名映射为嗯 |
| 015 | 对。 | 嗯。 | unchanged | online「那」/ offline「な」→ 映射嗯 |
| 026 | 对。 | 嗯。 | cut_now | 同义短回应 |
| 010 | 好。 | 嗯，没错。 | cut_now | 扩写，非空译 |
| 020 | 我想 | 哦，了解。 | cut_now | 短句听成别的话 |
| 027 | 我也很喜欢。 | 也去一下吧。 | cut_now | 短句听成别的话 |

结论：短句失分多为 **同义互换或模型上限**，不是 empty_tgt；**不许**把「对↔嗯」改成算对（改考卷须单独立项）。

## 4. 碎切基线

| 集 | multi_utt_rate | fragment_lost_rate | fragment_dup_rate |
|---|---:|---:|---:|
| Test zh_en | 0.20 | **0.00** | **0.00** |
| Dev zh_en | 0.10 | 0.03 | 0.00 |

`multi_utt`≠坏；Test 无后半句丢失、无相邻段重复。本轮不动切句。

## 5. 红线复核

- VAD 0.4 / 0.45 / 0.035：未改
- `MIC_DENOISE=False`：未改
- `mt_usable` / `asr_ok≤0.35`：未改
- 未用 Dev 分数改 Test TM/postedit；未动 `ASR_2PASS_*`

## 下一步（本刀之外）

1. **泛化优先于调参**：扩 holdout / 换抽样，勿在 Test 上加规则抬分  
2. 长句 4 条属模型能力上限；换模型或数据前不要拧 merge 权重  
3. 短回应若产品要「肯定回应同桶」，单独提案改尺子，不塞进 silent KPI 改动  
