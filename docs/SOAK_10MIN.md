# 10 分钟长稳回放（soak）

日期：2026-11-06  
脚本：`scripts/soak_10min.py`  
会话：`data/sessions/20261106-144837`  
命令：`run.py --mode dualsim --file cache/soak/soak_10min.wav --no-tts --subtitle --subtitle-path cache/soak/subtitle.json`

## 输入构成

| # | session | 时长 |
|---|---|---|
| 1 | `20261105-160805` | 13.9s |
| 2 | `20261105-163258` | 255.9s |
| 3 | `20261106-130636` | 180.0s |
| 4 | `20261106-131540` | 62.2s |
| 5 | `20261105-163258`（重复，凑 ≥600s） | 255.9s |

- 段间 0.5s 静音  
- 输出：`cache/soak/soak_10min.wav`（16k 单声道）  
- **段数 5，总时长 769.9s（≈12.8 min）**  
- 墙钟：`wall_s=63.9`（dualsim 不节流，远快于实时）

## 30s 采样表

| t(s) | RSS(MB) | threads | utt 数 |
|---:|---:|---:|---:|
| 0.0 | 3.4 | 4 | 0 |
| 30.2 | 1202.0 | 45 | 5 |
| 60.3 | 1230.2 | 42 | 29 |

原始：`cache/soak/samples.jsonl`、`cache/soak/summary.json`。

## 六条判据

| # | 判据 | 结果 | 说明 |
|---|---|---|---|
| 1 | 正常退出 exit 0，无 traceback | **PASS** | `exit_code=0`；`child_stdout.txt` 无 Traceback |
| 2 | RSS 末−首 &lt; 150MB，且最后 3 分钟斜率 &lt; 5MB/min | **FAIL** | 末−首 = **+1226.8MB**（t=0 为模型加载前，含 ASR/MT 装载）。稳态两点（30.2→60.3）：Δ=**+28.2MB**，两点斜率≈**56MB/min**。样本仅 2 点、墙钟仅 ~1min，**不能据此断定泄漏**，但按字面判据不达标 |
| 3 | 线程数波动 ≤ 2 | **FAIL** | 全程 4→45（span=41，加载期）。稳态 45→42（span=**3**）仍 &gt; 2 |
| 4 | `live.jsonl` 行数 == stdout `[utt]`（±1） | **FAIL** | live=**34**，`[utt]`=**30**（差 4）。其中 4 条 src/tgt 为空或半空（`''` / `嗯` 无 tgt），未打 `[utt]` 属预期差异，但判据字面不达标 |
| 5 | 结束时 `subtitle.json` 合法且 `state=idle` | **PASS** | JSON 合法，`state=idle`，`history` 6 条 |
| 6 | `[subtitle] write failed` = 0 | **PASS** | 本轮 0 次。更早一次试跑曾出现 1 次 `WinError 5` 改名失败（Windows 偶发），记入可疑点 |

## 必须写明的干扰项

1. **dualsim 不节流**：partial / final 速度快于真实会议；字幕队列 `maxsize=8` 满则丢最旧。若 history 缺最后几句，是回放速度造成的假象，不是 real-time 缺陷。本轮结束 history 仍有 6 条（展示上限），未观察到「尾句丢失」需单独追责的证据。  
2. **音频重复了 `163258` 段**：TM 会命中重复句，MT 调用数偏低属预期。本轮 stdout `[MT` 行 33、`[utt]` 30，与重复段 TM 命中并不矛盾（未做逐句 MT 去重审计）。

## 可疑点清单

- **RSS 装载后仍有 +28MB / 30s**：样本过稀，下次 soak 建议加长墙钟采样（或 `--pace` 实时节流专用 soak）再判斜率。  
- **线程稳态 span=3**：略超阈值，需更多采样点确认是否抖动。  
- **live 行数 &gt; `[utt]`**：空/半空 harvest 行进 live，与展示 `[utt]` 口径不一致——若要对齐需另开文档约定，本轮不改代码。  
- **字幕 `WinError 5` 偶发**（试跑 1 次）：`tmp→json` replace 拒绝访问；本轮未复现。  
- **墙钟仅 64s**：未覆盖真实 30–60min 的时间轴压力；本轮验证的是「~13min 音频全速灌入」而非「开麦挂 10 分钟」。

## 结论（一句话）

进程能跑完且字幕落盘 idle，但 RSS/线程按字面阈值未过；要回答「真开麦挂 10 分钟是否稳」，需要节流或实麦长挂，而不是 dualsim 全速。
