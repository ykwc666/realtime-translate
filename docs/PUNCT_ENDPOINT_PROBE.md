# P1-3 探针：标点模型能否替代「星期」白名单？

日期：2026-11-04  
模型：`models/sherpa-punct-ct-zh-en/model.int8.onnx`（CT-Transformer zh-en int8）

## 结论

**不能。** 默认仍用 `is_weekday_incomplete` 开补全窗口。标点模型只接 **展示层**（`ASR_PUNCT_DISPLAY`），MT 继续吃裸 ASR。

## 为何评审写的 OnlinePunctuation 对不上

| 名称 | 实际 | 语言 |
|------|------|------|
| `OnlinePunctuation` | cnn_bilstm + bpe | **仅英文** |
| 已下载 CT-Transformer | `OfflinePunctuation` | 中英 |

P1-3 设想的「在线标点判未说完」在 sherpa-onnx 里没有中文 Online 对应物。

## 证据

### 1) `add_punctuation` 总会加句末标点

| 输入 | 输出 |
|------|------|
| 现在是星 | 现在是星。 |
| 现在是星期 | 现在是星期。 |
| 现在是星期二 | 现在是星期二。 |
| 延迟大概有多少 | 延迟大概有多少？ |

「未说完」与「说完」都被标成句终 → 无法驱动 `WAIT_COMPLETION`。

### 2) 末字 logits（`_|，|。|？|、`）也分不开

`scripts/_probe_punct_logits.py`：

| 文本 | term(。？) | soft(_，、) | 问题 |
|------|------------|-------------|------|
| 现在是星（应等） | 0.47 | 0.54 | soft 略高 |
| 现在是星期（应等） | 0.60 | 0.40 | 偏「完整」 |
| 现在是星期二（应收） | 0.49 | 0.51 | 与上几乎同分布 |
| 现在是星期三（应收） | 0.43 | 0.57 | argmax=， 反而像未完 |
| 明星（应收） | 0.36 | 0.64 | 若用 soft 阈值会误等 |

没有稳定阈值能同时保住气口句、又不误伤完整句。

### 3) 标点喂给 MT 无净收益

`现在是星期` → `It's a week.`；加。后 → `It is now week.`（更差）。多数问句有无 `？` 译法相同。

## 落地

- `app/punct.py`：懒加载 OfflinePunctuation  
- `ASR_PUNCT_DISPLAY=True`：只改 `stats.source` 展示  
- 端点 / arbiter：**不**读标点模型  
- 复现探针：`.\.venv\Scripts\python.exe scripts\_probe_punct_logits.py`
