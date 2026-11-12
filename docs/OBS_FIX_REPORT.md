# OBS_FIX 验收报告

基线：`5a73f0a`。只改观测；`asr_stream.py` / `config.py` 零改动。

## 报数（A–H）

```
A pytest:        173 passed
B pick 分布:     Test zh_en {offline:70, online:1, merge:2} / Test en_zh {offline:64, online:2} / Dev {offline:140}
C 三条 merge:    zh_en 002（partial 补全，src 多「的」）; zh_en 027（标点吞尾）; en_zh 054 本跑未现（见下）
D KPI 差异:      mt_ok / asr_ok_le_0.35 两集两方向 0 项不同；Test en_zh asr_err_mean 0.2309→0.2323（ASR 抖动，ok 计数不变）
E src_raw diff:  1 条（Test 027，src_raw 长 7 字）
F asr_err_raw:   1 条 > 0.01（同 027：0.5217 → 0.3043）
G 吞尾日志:      1 次（Test 027 `[punct drop 7字]`；detail.punct_dropped=True）
H git diff:      app/asr_stream.py 改动 0 行，app/config.py 改动 0 行
```

### C / B 与任务书预期的差

任务书期望 Test en_zh `{offline:64, online:1, merge:1}`，merge 为 **054 假名 `うん→Yeah`**。

本跑 054：`on=嗯` / `off=.` / `src=嗯。` → 合法标成 **online**（offline 空内容，策略回退 online）。**未改代码凑数。**

### 修正前后 pick 正确率（2.3 口径：两路 norm 后不同）

| | 两路不同 | pick 对（on/off） | merge 例外 |
|---|---:|---:|---:|
| 修正前（任务书） | 210 | **4** | — |
| 修正后（本跑） | 210 | **209** | **1**（027 吞尾；002 的 on/off norm 后相同不进分母；054 本跑无映射） |

## 改动摘要

- `app/dual_track.py`：`classify_arbiter_pick` 去标点比对；`display_src` 二次 stamp；`src_raw` / `punct_dropped`；提前 return 路径补字段
- `scripts/eval_dialogue_mslt.py`：`utts[].src_raw`、顶层 `src_raw` / `asr_err_raw`（KPI `asr_err`/`asr_hyp` 不动）
- `tests/test_obs_arbiter_pick.py`
