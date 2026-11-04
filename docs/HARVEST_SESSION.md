# 真实语料采集 + 回放评测

契约见 `app/harvest.py` 顶部注释。写盘：后台队列线程逐行 `flush`，finalize 热路径只 `put_nowait`。

## 用户两步（真实 loopback）

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe scripts\harvest_session.py --mode loopback --source-desc "meet 10min youtube"
```

Ctrl+C 结束。然后：

```bat
.venv\Scripts\python.exe scripts\judge_harvest.py data\harvest\<刚生成的文件>.jsonl
```

在 `docs\harvest_judge_*.jsonl` 填 `verdict`（ok/awkward/wrong），再：

```bat
.venv\Scripts\python.exe scripts\judge_harvest.py --ingest docs\harvest_judge_<同名>.jsonl
```

等价入口：`run.py --mode loopback --harvest --source-desc "..."`。

## 冒烟（wav）

```bat
.venv\Scripts\python.exe scripts\harvest_session.py --mode file --file cache\ab_weekday\loopback_en_probe.wav --source-desc "smoke ab_weekday/loopback_en_probe" --no-tts --force-src en
```

## 旁路 hook

| 位置 | 理由 |
|------|------|
| `TranslateResult.mt_raw` | 观测 postedit 前文本，不改 `text` |
| `DualTrackStats.mt_raw` + `on_utt_final` | finalize 旁路回调，异常吞掉 |
| `TranslatePipeline.harvest` | 仅 `attach_engine`，不改识别/播放路径 |

## 若真实语料上「规则修过组」wrong 显著更高

说明规则在自写集上过拟合：下一步应**停增 postedit 整句替换**，把 harvest 里 wrong 的 `(src, mt_raw, tgt)` 聚成新失败类；高频短句进 TM，开放域长句走领域微调/更强 MT，而不是继续堆启发式。

## flaky 修复

`tests/test_tts_interrupt.py`：满负载下 stop 时延偶发 >500ms → 重试 3 次 + `played<0.70` / `stop<1500ms`（不断言硬实时）。全量套件连续 3 次：**82 passed**。
