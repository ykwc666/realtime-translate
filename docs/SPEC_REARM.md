# Speculate re-arm（气口句 miss_grow 悬挂修复）

日期：2026-11-04

## 根因（diag_spec_rearm.py）

`holding_cut` 路径只 `buf.append`，**不**调用 `_note_chunk_energy`。

`pause_xingqi_er` trace：

1. 气口 `sil=0.10` 启动 spec（`n0=22400`）。
2. arbiter 进入 `holding_cut`；续说 chunk `rms=0.30` 时 `engine_sil` 仍卡在 `0.50`（`vad_sil` 亦为 0）。
3. `_maybe_speculate` 每 chunk 都跑（`maybe+2/+3`），但 `trail_sil=max(vad,eng)=0.5 ≥ min_sil(0.08)`，**清理分支永不进入** → `cancel_speech=0`。
4. CUT 时 buffer 已含续说 → `miss_grow`，串行 final，`asr_wait≈250–290ms`。

这是「holding_cut 跳过能量钟」机制，不是「maybe 没被调用」。

## 修复

1. `holding_cut` 分支同样 `_note_chunk_energy`，并在 arbiter tick 前再 `_maybe_speculate`（续说可立刻 `cancel_speech`）。
2. 静音条件恢复后自然 re-arm；`ASR_SPECULATE_MAX_STARTS=3`；`restarts` 计数。
3. restart job 在 `_take_speculate` **不走** `skip_headstart`（否则 paced 下 age≈0.10 会扔掉第二枪，再付一遍串行 2pass）。

未改 VAD / arbiter / MT / TTS / miss_grow 语义。

## Counters（silence=0.45，paced，见 `docs/spec_rearm_accept.json`）

| wav | before (sil0.45) | after |
|-----|------------------|-------|
| pause_xingqi_er | starts=1 hits=0 cancel=0 miss_grow=1 asr_wait≈250–336 | starts=2 restarts=1 hits=1 cancel=1 miss_grow=0 asr_wait≈335–428 |
| stop_zheshi_xingqi | hits=1 | hits=1（不变） |
| cont_tuesday | hits=1 | hits=1（不变） |
| accept_phrases | starts=5 hits=4 cancel=1 | starts=5 restarts=1 hits=4 cancel=1 |
| tail_duosimo | hits=2 | hits=2 |

`pause` 的 `asr_wait≤50` **未达到**：re-arm 第二枪在 `completion_exhausted` 前约 0.10s 才启动，2pass 预计 ~785ms，CUT 后仍需 drain ~300ms。相对基线：由 miss 串行变为 **hit + 同量级 wait**，且 `mt_ready` 有改善（约 770→440）。

## 若未来 VAD=350ms

第二枪在续说后 `sil≥0.08` 才启动，解码墙钟仍约 0.7–0.8s；350 比 450 更少提前量 → **在 cut 时刻 job 已完成的命中率仍约 0%**；靠 restart 免 skip 仍可最终 hit，但 `asr_wait` 不会优于现在。

## 测试

- `tests/test_spec_rearm.py`：holding 续说 clear + re-arm 快照；`MAX_STARTS` 封顶。
- `scripts/diag_spec_rearm.py`：只读 trace（monkey-patch，默认不污染生产日志）。
