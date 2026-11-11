# Cursor 任务：降噪 A/B 离线判定（AMI + FLEURS）

**这份任务只做一件事：用现成语料判定 GTCRN 降噪到底帮不帮，不接线。**

## 0. 背景（为什么是这条）

- `app/denoise.py::MicDenoiser` 已封装 GTCRN 流式降噪；`models/gtcrn_simple.onnx` 已在本地（524KB）
- `app/config.py:34` `MIC_DENOISE = False`，生产默认关
- `docs/MIC_DENOISE.md` 的状态写的是「**待测**：实麦开/关对比」——**它从来没在任何真实会议音频上验证过**
- 现有基线：AMI 27/48 ok、WER 0.4433；FLEURS 11/12、WER 0.1352；mt_only 45/48
- AMI 是真实会议录音（房间噪声、混响、远场），正是降噪该起作用的场景

成本约 10 分钟，正负都是收获：好就打开一条路，差就永久关掉这个猜测。

## 1. 目标 / 非目标

**目标**：给出「开 GTCRN 后 AMI / FLEURS 的 ok 与 WER 变化」的可复现数字 + 报告。

**非目标**：不改生产行为。`MIC_DENOISE` 保持 `False`；`app/` 下除 `app/config.py` 一行不动（本任务不改它），唯一的代码改动是给 `scripts/eval_ledger.py` 加一个参数。

## 2. 任务 A｜造降噪语料 + manifest

新建 `scripts/build_denoise_corpus.py`。实现顺序：

1. 读 `data/ledger_corpus/manifest.jsonl`，只取 `corpus in {"ami","fleurs"}`（应为 48 + 12 = 60 条）
2. **构造降噪器之前打开开关**，且未生效就立刻失败退出——绝不允许静默直通。直通会产出一份与原音频相同的语料，然后被误读成「降噪无影响」：

   ```python
   from app import config
   config.MIC_DENOISE = True          # 必须在 MicDenoiser() 之前
   dn = MicDenoiser()
   if not dn.enabled:
       sys.exit("[abort] denoiser not enabled — 检查 models/gtcrn_simple.onnx")
   ```

   日志里必须出现 `[denoise] GTCRN online frame=`。

3. 逐条：`load_wav(path)`（`app/audio_io.py:55`，返回 1-D float32 @16k）→ 按 **100ms 一块**（1600 samples @16k）喂 `dn.process()`，收集非空输出 → 结束 `dn.flush()` → 拼接
   （`MicDenoiser` 内部按 frame=256 samples 缓冲切帧，块大小只影响调用次数，不影响结果）
4. 写 `data/eval/denoise_ab/dn/<id>.wav`：16-bit PCM、采样率与声道与源一致（应全为 16k/mono）。**时长偏差 > 0.05s 直接 abort**（说明帧对齐错了，继续跑就是白跑）
5. 生成 `data/eval/denoise_ab/manifest.jsonl`：60 行；字段逐字复制源 manifest，**只改 `path`**（写成仓库相对路径，如 `data/eval/denoise_ab/dn/ami_000.wav`）；`id` / `ref` / `corpus` 必须与源一一对应
6. 打印 RMS 诊断（这条最有用，先看它再解读结果）：
   - 每条 `rms(dn) / rms(raw)`
   - 报 `p10 / p50 / p90`
   - 统计比值 **< 0.3** 的条数（这类基本等于把话削掉了，ASR 必然更差）
7. 脚本可重复运行，第二次覆盖不报错

## 3. 任务 B｜`eval_ledger.py` 加 `--out-dir`

现在 `OUT_DIR = ROOT / "docs"`（`scripts/eval_ledger.py:49`）是硬编码，跑 A/B 会覆盖 `docs/ledger_eval_summary.json` 与 `docs/ledger_eval_detail.jsonl` 这两份**已提交的基线**。

- 新增 `--out-dir`（`type=Path`，**默认 `docs`**）
- `detail_path`（现 `:489`）与 `sum_path`（现 `:493`）都基于它
- 不传时行为必须与现在完全一致

## 4. 跑两臂

```bat
cd /d D:\xuanjian\realtime-translate

:: 臂 1：原语料（复核基线，必须先跑）
.venv\Scripts\python.exe scripts\eval_ledger.py --corpus ami,fleurs --skip-a5 --out-dir data\eval\denoise_ab\raw

:: 臂 2：降噪语料
.venv\Scripts\python.exe scripts\eval_ledger.py --manifest data\eval\denoise_ab\manifest.jsonl --corpus ami,fleurs --skip-a5 --out-dir data\eval\denoise_ab\dn_out
```

判定器是本地规则（不联网），两臂口径天然一致，不需要额外固定随机种子。

## 5. 闸门

| # | 判据 | 不过怎么办 |
|---|---|---|
| A-1 | 日志有 `[denoise] GTCRN online frame=`，且 `dn.enabled=False` 时脚本会 abort | 修脚本，不许往下走 |
| A-2 | 生成 60 个 wav，全部 16k/mono/16bit，时长偏差 ≤0.05s | 修帧对齐，重跑 |
| A-3 | 降噪 manifest 60 行，`id`/`ref`/`corpus` 与源逐条相同，仅 `path` 不同 | 修 |
| A-4 | RMS 比值 `p10/p50/p90` 已打印，`<0.3` 条数已给出 | 补 |
| B-1 | 不传 `--out-dir` 时，`docs/` 两份输出与 HEAD 版本**字节级一致** | 修默认值 |
| C-1 | **臂 1 必须复现基线**：AMI 27/48、WER 0.4433±0.005；FLEURS 11/12、WER 0.1352±0.005；mt_only 45/48 | 管线或环境有问题：**停**，不许解释降噪结果 |
| C-2 | 跑完后 `git status` 中 `docs/` 只有新增的 `DENOISE_AB.md` | 修 |
| C-3 | `pytest` ≥ 149 passed | 修 |

**预登记判定规则（先写死，不许事后改）**

| 结论 | 条件（三条同时成立） |
|---|---|
| **采用** | AMI ok ≥ **31/48** 且 AMI WER ≤ **0.4133** 且 FLEURS ok ≥ **11/12** |
| **否决** | AMI ok ≤ **27/48** 或 AMI WER ≥ **0.4433** 或 FLEURS ok ≤ **10/12** |
| **不确定** | 以上都不满足 → 只报数字，**不许接线** |

阈值依据：baseline 27/48、0.4433；31/48 是 +4 条，超出我们已知的 ASR 抖动（AMI 约 1/60 条流式不可复现）一个量级，才算真信号。

**本任务不接线**：`MIC_DENOISE` 保持 `False`，要不要开由下一轮单独决定。

## 6. 报告 `docs/DENOISE_AB.md`

必须包含：

1. 两臂对照表（AMI / FLEURS × ok、ok_rate、WER、mt_only、empty_n）
2. RMS 比值表（p10 / p50 / p90 + `<0.3` 条数）
3. **5 条逐条样例**：`ref` / `raw asr` / `dn asr`。挑 WER 变化最大的 2 条 + 变好的 2 条 + 1 条无变化的
4. 按第 5 节规则给出结论：**采用 / 否决 / 不确定**，并写清触发的是哪几条数字
5. 一句话机制解释：如果结论是「否决」，明确写「GTCRN 不适用于真实会议音频，远场路线应换方向（远端降噪 / 更强 ASR），不再重复这条路」

## 7. 提交

```
feat(eval): add --out-dir so A/B runs don't overwrite docs baselines
feat(denoise): offline corpus builder for AMI/FLEURS denoise A/B
docs: denoise A/B on AMI and FLEURS (<结论一句话>)
```

三个提交分开，报告单独一个。`data/eval/` 加进 `.gitignore`（生成物约几 MB，不入库）。

## 8. 禁止

- 改 `MIC_DENOISE` 默认值、改 `app/pipeline.py`、改 `app/denoise.py` 的算法
- 动 VAD 三件套（`0.4` / `0.45` / `0.035`）、25s 块长、`ASR_LOOP_*`、`ASR_SPEC_*`、`prefer_2pass`
- 动 TM / postedit 规则、字幕、README、`start.bat`
- 只跑 AMI 不跑 FLEURS；只跑降噪臂不复核原语料臂
- 结果不利时去调 beam / 阈值 / 判定条件凑绿
- 把「不确定」写成「有效」

## 9. 预估

| 步骤 | 耗时 |
|---|---|
| 写两个脚本 | 主体 |
| 造语料（60 条，1–11s） | ~20s |
| 两臂评测（各 60 条） | 各 ~70–90s |
| 报告 | — |

两臂总评测时间 < 4 分钟。
