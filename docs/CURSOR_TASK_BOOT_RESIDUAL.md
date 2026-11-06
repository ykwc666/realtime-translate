# CURSOR 任务书：启动耗时收尾（口径 / 静默 / 落盘测试）

> 上一轮（`1b70852` + `594f4e0`）的 A 项验收通过，B 项**只完成一半**。
> 本任务书接 B 的尾巴，三件事：**先把尺子统一（C）→ 再消掉真静默（D）→ 补落盘契约测试（E）**。
> C 必须先落地并跑出基线，再做 D，否则省了多少秒说不清。

---

## 0. 已实测的事实（照抄即可，不要重复排查）

在本机、当前 venv、`--no-tts` 下带时间戳跑 `TranslatePipeline()`，实测时间线：

```
[  0.00s] 进程起
[  6.06s] import app.pipeline 完成          ← 6.06s 全静默，无任何输出
[  6.06s] 加载模型中（CPU）ASR=… MT=ct2 …   ← pipeline t0 从这里开始
[  8.10s] [load] paraformer 在线识别就绪 2.0s
[ 10.10s] [load] sensevoice 2pass 就绪 2.0s
[ 21.76s] [load] MT 中↔英 就绪 1.1s         ← 与上一行之间 10.66s 全静默
[ 22.22s] [punct] loaded model.int8.onnx in 350ms
[ 22.23s] 模型就绪，用时 16.2s               ← 只统计 6.06s→22.23s，不含前面 6.06s
```

**那 10.66s 是什么**：`app/translate.py:56-57`

```python
import ctranslate2                                    # 0.2s
from transformers import AutoTokenizer                # ≈10.4s   ← 元凶
```

这两行都在 `t0 = time.perf_counter()`（`app/translate.py:62`）**之前**，所以完全不计入
`[load] MT 就绪 1.1s`。全项目运行时只有这一处 import transformers。

**三处需要纠正的旧结论**（本任务书以本节为准）：

1. `[punct]` **有**打印（`app/punct.py:68`），耗时 350ms，不是"没打点"，也不是洞。
2. B 的空白不是"标点 + 前段"，就是上面那 10.4s 的 transformers 导入。
3. 汇报里的 `29.9s` 与之前的 `37.5s` **不是同一把尺子**：29.9s 从 pipeline `t0` 起算，
   不含约 6s 的依赖导入；37.5s 是外部从进程启动计时。所以"37.5 → 29.9 快了 7.6s"
   **不成立**，两者不能相减。

---

## 1. 任务 C｜统一启动计时口径（纯打印，零风险，先做）

### 要改成什么

1. `run.py` 顶部（`from __future__ import annotations` 之后、`import argparse` 之前）加：

   ```python
   import time as _time

   PROCESS_T0 = _time.perf_counter()   # 进程内最早可用时刻
   ```

2. `app/pipeline.py` 的 `TranslatePipeline.__init__` 保留现有 `t0`（模型初始化段），
   新增一个可选参数 `process_t0: float | None = None`，由 `run.py` 传入 `PROCESS_T0`。

3. 在 `print(f"模型就绪，用时 …")` 这行**保持原文不动**（`docs/ARCHITECTURE_REVIEW.md:135`
   与 `docs/CURSOR_TASK_TTFA_WARMUP.md` 引用了它），**另起一行**打印：

   ```
   [boot] 依赖导入 6.1s | 模型初始化 16.2s | 进程总 22.3s
   ```

   其中：`依赖导入 = t0 - process_t0`，`模型初始化 = now - t0`，`进程总 = now - process_t0`。

4. `run.py` 里 `from app.pipeline import TranslatePipeline` 这行之前，加一行
   `print(f"[boot] 开始导入依赖 …", flush=True)`（`run.py:123` 上方）。

### C 的闸门

| 闸门 | 判据 |
|---|---|
| C-1 | 启动 3 次，每次都出现 `[boot] 依赖导入 … \| 模型初始化 … \| 进程总 …`，且 `|依赖导入 + 模型初始化 − 进程总| ≤ 0.5s` |
| C-2 | `模型就绪，用时 X.Xs` 这行**逐字符不变**，`双轨实机 VAD：…` 这行也逐字符不变 |
| C-3 | 3 次 `进程总` 波动 ≤ 3s（不能出现 60s 那种偶发） |
| C-4 | `pytest` ≥ 144 passed |

**C 阶段必须报三次原始时间线**，这是 D 的基线。做完先单独提交，再做 D。

---

## 2. 任务 D｜去掉 CT2 路径的运行时 transformers 依赖（省 ≈10.4s）

### 为什么可行（已在本机验证，别再自己论证一遍）

`models/opus-mt-zh-en/` 和 `models/opus-mt-en-zh/` 都有 `source.spm` / `target.spm`，
`sentencepiece 0.2.2` 已在 `requirements.txt` 里且已安装。

- 分词：`sp.encode(text, out_type=str)` 与 `AutoTokenizer.encode+convert_ids_to_tokens`
  **完全一致，唯一差别是 Marian 会多一个结尾 `</s>`**。补上即可。
- 反分词：``</s>` 的 `sp.is_control()` 为 `True`，`sp.decode()` 自身已丢弃，无需额外过滤。
- 端到端：我用两套 tokenizer 跑同一个 CT2 模型，zh→en 5 条 + en→zh 3 条，
  **8/8 输出逐字符相同**。

### 要改成什么

1. 新增 `app/mt_spm.py`，提供与 `AutoTokenizer` 同形的四个方法，**只覆盖 `_translate_ct2`
   用到的接口**：

   ```python
   class SpmTokenizer:
       def __init__(self, source_spm: Path, target_spm: Path) -> None: ...
       def encode(self, text, *, truncation=False, max_length=None) -> list[int]  # 末尾补 </s> 的 id
       def convert_ids_to_tokens(self, ids) -> list[str]                          # sp.id_to_piece
       def convert_tokens_to_ids(self, tokens) -> list[int]                       # 未知 piece → unk_id
       def decode(self, ids, *, skip_special_tokens=True) -> str                  # sp.decode
   ```

   注意：`encode` 补 `</s>` 是硬要求（Marian 行为），`max_length` 按截断语义实现
   （**截断后再补 `</s>`**，保持与 `AutoTokenizer(truncation=True)` 一致）。

2. `app/translate.py` 的 `_init_ct2()`：
   - 删掉 `from transformers import AutoTokenizer`（第 57 行）
   - 两处 `AutoTokenizer.from_pretrained(...)` 换成 `SpmTokenizer(root/"source.spm", root/"target.spm")`
   - 保留 `t0` 与 `[load] MT 中↔英 就绪` 打印
   - **`_init_transformers()` 一个字都不许动**（回退路径）

3. `scripts/check_mt_tokenizer_ab.py`（新增）：对同一批文本，用 transformers tokenizer
   和 `SpmTokenizer` 各跑一遍同一个 CT2 模型，逐条比对最终字符串，打印 `SAME/DIFF` 计数。
   要求能直接跑：

   ```
   .venv\Scripts\python.exe scripts\check_mt_tokenizer_ab.py
   ```

   语料（字段名已核对，直接用）：

   | 文件 | 行数 | 方向 | 输入字段 |
   |---|---|---|---|
   | `data/mt_call_train.jsonl` | 84 | zh→en | `zh` |
   | `data/mt_call_eval.jsonl` | 22 | zh→en | `zh` |
   | `data/mt_call_eval_en_zh.jsonl` | 60 | en→zh | `en` |

   只比对**模型输出字符串**（`_translate_ct2` 的返回值），不过 postedit、不过 TM、不比对参考译文。

### D 的闸门

| 闸门 | 判据 |
|---|---|
| D-1 | `check_mt_tokenizer_ab.py` 输出 **DIFF = 0**（zh→en 与 en→zh 分别统计，各为 0） |
| D-2 | 3 次启动，`[load] sensevoice` 与 `[load] MT` 之间的间隔 **≤ 1.5s**（基线 10.66s） |
| D-3 | 3 次 `模型就绪，用时` 均 **≤ 20.0s**（基线 29.7 / 29.7 / 29.9） |
| D-4 | 3 次 `[boot] 进程总` 相对 C 阶段基线均下降 **≥ 8.0s** |
| D-5 | `pytest` 全绿，且数量 = 144 + E 新增 |
| D-6 | `python -c "from app.translate import Translator"` 通过；`MT_BACKEND="transformers"` 分支仍可 import（不要求跑） |

**任何一项不过 → 整体回退**（`git revert` 这一提交），不许保留半套实现，
不许把 `transformers` 从 `requirements.txt` 删掉（训练/bench 脚本仍用）。

---

## 3. 任务 E｜落盘契约测试（补洞）

`1b70852` 改的是"行数契约"，但 `tests/` 里没有任何 `HarvestWriter` / `SessionRecorder` 测试，
现在只有人工跑 10 分钟在守。补两个文件，**不允许 sleep 超过 0.2s**，不允许起麦克风。

### `tests/test_harvest_persist.py`

用 `tmp_path` 造路径，`HarvestWriter(path, mode="vad", source_desc="t")`，用
`types.SimpleNamespace` 造 stats（字段见 `app/harvest.py:49` 的 `row_from_stats`）。

| 用例 | 断言 |
|---|---|
| E-1 行数守恒 | 连续 `emit_from_stats` 94 条（src 各不同）→ `close()` → 文件恰好 94 行，`src` 集合与输入一致，无重复 |
| E-2 延迟回填 | 第 1 条 stats 的 `latency_device_ttfa_ms=None`；挂起后再把**同一个对象**改成 `673.0`；`close()` → 该行落盘值为 `673.0`（这是延后落盘的核心契约） |
| E-3 空 src 不写 | `emit_from_stats(SimpleNamespace(source="   "))` → `close()` → 0 行 |

### `tests/test_live_persist.py`

`SessionRecorder(session_dir=tmp_path/"s1", mode="vad")`，同样用 SimpleNamespace。

| 用例 | 断言 |
|---|---|
| E-4 live 行数 | `emit_live` 3 条 → `close()` → `live.jsonl` 恰好 3 行，字段 `src` 顺序一致 |
| E-5 尾行回填 | 最后一条的 `latency_device_ttfa_ms` 在 `close()` 前被修改 → 落盘看到新值（尾行不被丢） |

---

## 4. 禁止（红线，一项都不要碰）

- `VAD_TEN_THRESHOLD` / `VAD_SILENCE_SEC` / `VAD_SPEECH_RMS_MIN` 三件套
- 25s 块长、`ASR_LOOP_*`、`ASR_SPEC_*`、`prefer_2pass`
- TM（`app/mt_tm.py`）与 postedit（`app/mt_postedit.py`）任何规则
- MT 解码参数（`MT_DECODE_*` / `MT_BEAM_*` / `MT_NO_REPEAT_*` / `MT_REPETITION_*`）
- 字幕（`app/subtitle.py`、`scripts/subtitle_server.py`）与 README / start.bat
- `models/` 下任何文件（不许删、不许转、不许重建）
- `_init_transformers()` 与 `_translate_transformers()` 路径
- 为"看起来快"改口径：不许把 `模型就绪，用时` 改成只统计后段，不许把 `[load]` 行合并或删掉

---

## 5. 报告格式（照这个贴，不要自由发挥）

```
## C：口径基线（3 次）
BOOT 1: [boot] 依赖导入 X.Xs | 模型初始化 Y.Ys | 进程总 Z.Zs
        （[load]/[punct]/模型就绪 原文照贴）
BOOT 2: …
BOOT 3: …
C 闸门: C-1 PASS/FAIL  C-2 …  C-3 …  C-4 …

## D：tokenizer A/B
zh→en: SAME=n DIFF=0
en→zh: SAME=n DIFF=0
BOOT 1/2/3: [load] sensevoice→MT 间隔 X.Xs | 模型就绪 X.Xs | 进程总 X.Xs | 两次相减 −X.Xs
D 闸门: D-1 … D-6

## E：测试
新增用例数、pytest 尾行原文

## Commits
<hash> — <subject>   （C / D / E 必须分开提交，共 3 个）

## pytest 尾行
（原文）
```

三项任一不过，**如实写 FAIL 并停手**，不许改闸门、不许凑参数、不许编数字。

---

## 6. 建议提交信息

```
perf(boot): unify boot timing against process start
perf(mt): load CT2 tokenizers via sentencepiece, drop runtime transformers import
test(record): cover harvest/live row-count and late TTFA backfill
```
