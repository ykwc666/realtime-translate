# Re-arm 第二枪 offline-only（经济账修正）

日期：2026-11-04

## 审查结论：上轮该不该留？

| 部分 | 判定 |
|------|------|
| holding_cut 补能量钟 + cancel/re-arm 机制 | **保留**（根因修对） |
| 第二枪 `transcribe_with_2pass`（full） | **不保留为默认**（经济账负：asr_wait 与 CPU 都差于 miss→串行） |
| 本轮默认 `ASR_SPECULATE_REARM_MODE=offline` | **采取** |

## 数据流（offline 命中）

气口后 restart → 后台只跑 `transcribe_offline_only(snap)` → CUT 时并行：
`finalize_online_only(live state)` ∥ drain offline job → `prefer_2pass_asr(online, offline_text)`。  
首枪仍是完整 2pass；`miss_grow` 语义不变。

## pause_xingqi_er（paced，2 rep，并行 CUT 后）

| mode | asr_wait | cpu_ms | mt_ready | hit | ath_max |
|------|----------|--------|----------|-----|---------|
| **offline** | 193 / 273 | 607 / 694 | 335 / 437 | 1 | 240 / 344 |
| full（上轮） | 352 / 415 | 880 / 840 | 499 / 567 | 1 | 403 / 473 |
| off（无第二枪） | 253 / 290 | 323 / 336 | 395 / 444 | 0 | 300 / 350 |

- 相对 **full**：wait↓、cpu↓约 25–30%、ath_max↓。  
- 相对 **off**：wait 略好或持平，多烧第二枪 CPU；换来 hit=1。  
- **硬门 asr_wait≤50：未过。** 原因不是 oneshot，而是 `completion_exhausted` 后第二枪只有 ~100ms 提前量，offline 本身 220–340ms，CUT 时仍要等；再叠 online finalize（含 1s pad）墙钟。任务里「窗口 370ms」按 VAD 450 估算，本 fixture 走仲裁强切，装不下。

## mt_ready「770→440」澄清

- 上轮 accept JSON **无** `mt_ready` 字段；440 无法从该文件复现。  
- CUT 地板叙事也不成立：本句 `t_cut≈100`（能量钟修复后 sil 变短），mt_ready 实测 **335–570**，可低于 450。  
- **结论：上轮 770→440 不可采信**（口径/混比问题）。本轮三模式 mt_ready 见上表与 `docs/spec_rearm_pause_modes.json`。

## 若 VAD=0.35

offline 实测 ~220–340ms；仲裁强切提前量仍约 100ms 量级（与 VAD 地板弱相关）→ 预期 asr_wait 仍约 **150–250ms**，不会到 ≤50。

## 配置

```python
ASR_SPECULATE_REARM_MODE = "offline"  # offline | full | off
```
