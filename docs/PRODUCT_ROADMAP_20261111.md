# 产品开发顺序（2026-11-11）

> 先钉尺子 → 再对准靶子（双人对话）→ 再调枪。顺序错了，做得越多返工越多。

## 产品定位

- **现在**：对话式翻译（两人，一中一英，实时互译）
- **远期**：会议室 / 多人；AMI 只作参考，**非当前 KPI**

## 步骤

| 步 | 内容 | 状态 |
|---|---|---|
| 1 | 尺子：`gate_realtime_regression.py` 分进程×5 轮中位；交 n/p50/p90；两次 p50≤3% | **完成** `GATE_REALTIME_OK`（见 `docs/GATE_REALTIME_RULER.md`） |
| 2 | 双人对话 KPI 集：用 **MSLT** 公开语料抽样 40–60（非自录） | **完成** Test/Dev 各 120 条（zh_en 60 + en_zh 60，T1/T2 配对过滤后）；见 `DIALOGUE_BASELINE_MSLT_V2.md` |
| 3 | 降噪 A/B（`CURSOR_TASK_DENOISE_AB.md` + 音量比 + 分场景结论；生产保持关） | **完成 · 否决**（见 `docs/DENOISE_AB.md`）；`MIC_DENOISE=False` |
| 4 | 对话硬骨头：先统计半句占比再改；弱信号只许旁路默认关；不动 VAD 三阈值 | **V2 下一刀完成**。ASR 长尾 **STOP_MODEL_CEILING**。OBS_FIX + **PUNCT_GUARD**（吞尾≥4 字回退 ASR 原文；Test zh_en asr_ok 50→51）。见 `OBS_FIX_REPORT.md` / `PUNCT_GUARD_REPORT.md`。语料 V2 见 `DIALOGUE_BASELINE_MSLT_V2.md` |

## 明确不做

VAD 0.35 / early-cut / 标点端点；功能稳定前不做字幕大改、会后说话人、摘要。
