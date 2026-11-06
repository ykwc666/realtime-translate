# 英→中 准确度：分层数字与归因（2026-11-06）

语料：`data/ledger_corpus/manifest.jsonl` — **AMI 48**（真实多人会议，IHM 远场）+ **FLEURS 12**（朗读，近场）。
判定：`scripts/eval_ledger.py` 的 **model-judge（规则判定，不是大模型）**，见下文「口径」。

## 一、三个层次，别混着说

| 层次 | 语料 | 结果 | 含义 |
|---|---|---|---|
| **MT 单测**（直接喂正确英文原文） | AMI 48 | **45/48 = 93.75%** ok（2 awkward、1 wrong） | 纯翻译能力 |
| 同上 | FLEURS 12 | **11/12 = 91.67%** | 纯翻译能力 |
| **整条管线**（VAD→ASR→MT） | FLEURS 12 | **11/12 = 91.67%**，WER **0.1352** | 安静近场朗读 |
| **整条管线** | AMI 48 | **27/48 = 56.25%**，WER **0.4433** | 真实会议条件 |

一句话：**翻译本身 93–94% 可用；端到端掉到 56% 的那部分，几乎全是「没听清」。**

## 二、AMI 那 21 条「失败」的归因

| 原因 | 条数 | 说明 |
|---|---|---|
| `empty tgt` | **9** | ASR 一个字都没输出，后面无译可译 |
| `asr_wer > 0.45` | **10** | 听错太多，判定器直接判 wrong |
| `content_density < 1.5` | **2** | 判定器误判，见下 |

**这 21 条里没有一条是「听对了但译错」。** 那 2 条 `content_density` 实际上是正确译文：

```
ref : THAT'S A GOOD POINT
asr : That's a good point.          ← 听对了
zh  : 说得好                          ← 被判 wrong，因为太短

ref : BUT YEAH JUST I DON'T KNOW JUST IT DOESN'T MAKE ANY DIFFERENCE REALLY EITHER WAY TO ME
asr : It doesn't make any difference really either way to me.
zh  : 对我来说,这没什么区别              ← 同样只是「短」
```

## 三、把「听」这个变量拿掉之后

AMI 48 条里 ASR WER ≤ 0.3 的有 23 条，其中 **22 条通过 = 95.7%**。

所以正确的读法是：

- **翻译能力 ≈ 95%**（听对的情况下）
- **端到端、安静近场 ≈ 92%**
- **端到端、远场多人会议 ≈ 56%**

## 四、口径（引用数字前必看）

1. **`model-judge` 是规则判定，不是人工也不是 LLM**：`ASR WER > 0.45` 直接判 wrong、
   `content_density < 1.5` 判 wrong、中文占比不足判 wrong、重复 ≥3 次判 wrong。
2. 因为第一条规则，**pipeline 通过率天然被 ASR 决定**，不能当作「翻译质量」引用。
3. **WER 0.4433 不等于「翻译错 44%」**：它是字级错误率（含整句没听出来的部分），
   语义可用率是 56.25%，两个数是不同维度。
4. AMI 是 IHM 远场多人会议，FLEURS 是朗读；两者不可平均。

## 五、复现

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe scripts\eval_ledger.py --skip-a5 --corpus ami,fleurs
```

约 70 秒，重写 `docs/ledger_eval_summary.json` 与 `docs/ledger_eval_detail.jsonl`。

2026-11-06 16:59 在含 CT2 tokenizer 替换的 HEAD 上复跑：

- AMI **27/48**、WER **0.4433**；FLEURS **11/12**、WER **0.1352**；MT-only **45/48** —— 与 13:31 那份**逐条判定零翻转**
- 唯一差异：3 条句子的 ASR 标点抖动（`,` ↔ `。`），WER 四舍五入后一致

## 六、边界

- **只有 en→zh 有这套评测**；zh→en 目前只有实麦短句样本，不能下结论。
- 82 条主集仍然是「小样本」：3.5 个百分点的差异（1 条）不构成结论。
- 早期 `docs/LEDGER_EVAL_REPORT.md`（2026-11-04）里的 8.3% / 0.917 是空 ASR bug 修复前的快照，**已作废**。
