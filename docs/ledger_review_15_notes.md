# ledger_review_15 抽查笔记（2026-11-05）

来源：`docs/ledger_review_15.jsonl`。口径：**model-judge**，对照官方 `ref`。

## 结论（ASR 锅是否站得住）

**站得住。** 15 条里凡有 `mt_only_verdict=ok` 且 pipeline wrong 的，根因都在 ASR/切句，不在 MT 规则层。

| 类型 | 句例 | 现象 |
|------|------|------|
| VAD 漏检 | ami_000 / ami_001 | `src=""`，短句未检出 |
| 半句/功能词切断 | ami_031 | ref 很长，src 停在 `…you can only do。` |
| 识别崩成碎片 | ami_009 `weicen`、ami_017 `yes。`、ami_026 `right ghsome` | 相对长 ref 几乎无信息 |
| 语种串扰 | ami_004 | 英文会议被听成「你说也。」→ 空译 |
| 勉强 borderline | ami_030 / ami_033 | 有字但错乱/混码，覆盖薄 |

**切句相关典型**：ami_031（功能词结尾截断）、ami_000/001（漏检）。  
**不是过拟合 postedit**：这些句 `mt_raw` 路径上的失败发生在 ASR 之后；MT-only 对照 ref 基本可读。

## 对今日 A/B 的含义

连续会话上优先看：短碎句是否被 `VAD_MIN_UTT_SEC` 合并；漏检/半词是否减少；延迟是否可接受。
