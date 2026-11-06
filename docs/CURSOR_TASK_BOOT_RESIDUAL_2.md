# CURSOR 任务书（返工）：恢复 D、补一行提示、继续 E

> 上一轮（`ae7c238` 之后的工作）**C 通过、D 被整体回退、E 未做**。
> 回退是错的：D 的方向和实现都对，只是我给的闸门有两处写错了。
> 本任务书**不要求重新设计**，只做三件事：**把 D 恢复回来（F）→ 给 CT2 加载加一行提示（G）→ 补落盘测试（I）**。

---

## 0. 先看这组实测数据（我这边跑的，不用你再验证）

### 0.1 CT2 首次构造有 2–6.5s 一次性开销，且与模型无关

干净进程里连续 5 次构造 `ctranslate2.Translator`：

```
run 1: zh_en 6.39s | en_zh 0.18s
run 2: zh_en 4.96s | en_zh 0.19s
run 3: zh_en 4.37s | en_zh 0.19s
run 4: zh_en 2.06s | en_zh 0.19s
run 5: zh_en 6.42s | en_zh 0.18s
```

把顺序反过来验证：

```
先构造 models/opus-mt-zh-en-ct2          → 6.57s
后构造 models/opus-mt-zh-en-lora-call-ct2 → 0.19s
```

两个目录的文件**逐字节同尺寸**（`model.bin` 均为 79,567,635 字节）。
结论：**谁先构造谁付这 2–6.5 秒，这是 ctranslate2 的一次性全局初始化，不是模型加载，也无法通过换模型/换参数消除。**

这解释了上一轮 D-2 的 FAIL：`sensevoice → MT` 的 1.78 / 5.52 / 8.20s 间隔，
绝大部分是这 6 秒，不是 SpmTokenizer。原实现里它被 `import transformers` 的 10 秒
顺手付掉了，所以看起来永远是 `[load] MT 就绪 1.0s`。

### 0.2 「进程总」这个口径不可用

上一轮 C 阶段三次：

| 指标 | 三次值 | 极差 |
|---|---|---|
| 依赖导入 | 2.2 / 8.3 / 1.2 | **7.1s** |
| 模型初始化 | 15.5 / 15.3 / 15.3 | **0.2s** |
| 进程总 | 17.7 / 23.6 / 16.4 | **7.2s** |

噪声全部来自 `import app.pipeline` 之前（磁盘 / 杀毒 / 页缓存），与代码无关。
**所以原任务书 C-3、D-4 两条闸门是我写错了，本轮作废。唯一可信口径是「模型初始化」。**

### 0.3 用可信口径重看 D：三次全部更快

| | 模型初始化 |
|---|---|
| C 基线 | 15.5 / 15.3 / 15.3（中位 **15.3**） |
| D 实现 | 6.2 / 11.0 / 12.7（中位 **11.0**） |
| 逐次差 | −9.3 / −4.3 / −2.6 |

加上 D-1 的 **166 条零差异**（106 + 60），这是一个既有效又安全的改动。
回退等于白扔 2.6–9.1 秒。**本轮把它恢复。**

---

## 任务 F｜恢复 SpmTokenizer（恢复，不是重写）

### F.1 新建 `app/mt_spm.py`

只实现 `_translate_ct2` 用到的四个方法，不多做抽象：

```python
class SpmTokenizer:
    """用 sentencepiece 直接加载 Marian 的 source.spm / target.spm，
    替代 transformers.AutoTokenizer，避免 ~10s 的 transformers 导入。"""

    def __init__(self, source_spm: Path, target_spm: Path) -> None: ...

    def encode(self, text: str, *, truncation: bool = False,
               max_length: int | None = None) -> list[int]:
        """sp.encode(text, out_type=str) → piece_to_id → **末尾补 `</s>` 的 id**。
        有 max_length 时：先截到 max_length-1，再补 `</s>`（总数 ≤ max_length）。"""

    def convert_ids_to_tokens(self, ids) -> list[str]:   # sp.id_to_piece
    def convert_tokens_to_ids(self, tokens) -> list[int]:  # sp.piece_to_id，未知 → unk_id
    def decode(self, ids, *, skip_special_tokens: bool = True) -> str:
        """先过滤 sp.is_control(i) 为真的 id，再 sp.decode。"""
```

**三条硬要求**：

1. **`encode` 必须补结尾 `</s>`**。这是 Marian 的行为，`AutoTokenizer` 会自动加，
   `sentencepiece` 不会。少这一个 token，输出就会变。
2. `encode` 的截断语义要与 `AutoTokenizer(truncation=True, max_length=N)` 一致：
   总数（含 `</s>`）不超过 N。
3. `decode` 必须过滤 control token（`</s>` 的 `is_control()` 为 `True`，实测确认）。

### F.2 改 `app/translate.py` 的 `_init_ct2()`

- 删掉第 57 行 `from transformers import AutoTokenizer`
- 两处 `AutoTokenizer.from_pretrained(...)` 换成 `SpmTokenizer(...)`
- **spm 文件从 `config.OPUS_ZH_EN` / `config.OPUS_EN_ZH`（原始 transformers 目录）取**，
  不是从 CT2 目录取——CT2 目录里没有 spm 文件

```python
self._tok_zh_en = SpmTokenizer(config.OPUS_ZH_EN / "source.spm",
                               config.OPUS_ZH_EN / "target.spm")
self._tok_en_zh = SpmTokenizer(config.OPUS_EN_ZH / "source.spm",
                               config.OPUS_EN_ZH / "target.spm")
```

- `t0` 与 `[load] MT 中↔英 就绪` 打印保留
- **`_init_transformers()` 和 `_translate_transformers()` 一个字都不许动**

### F.3 新建 `scripts/check_mt_tokenizer_ab.py`

对同一批文本，用两套 tokenizer + 同一个 CT2 模型各跑一遍，逐条比对最终字符串。

| 文件 | 行数 | 方向 | 输入字段 |
|---|---|---|---|
| `data/mt_call_train.jsonl` | 84 | zh→en | `zh` |
| `data/mt_call_eval.jsonl` | 22 | zh→en | `zh` |
| `data/mt_call_eval_en_zh.jsonl` | 60 | en→zh | `en` |

- 只比对**模型输出**（`_translate_ct2` 的返回值），不过 postedit、不过 TM、不比对参考译文
- 输出形如 `zh→en: SAME=106 DIFF=0` / `en→zh: SAME=60 DIFF=0`
- 直接可跑：`.venv\Scripts\python.exe scripts\check_mt_tokenizer_ab.py`

这个脚本是这条改动的**守门员**，以后任何动 tokenizer 的改动都要先跑它。

### F 的闸门

| 闸门 | 判据 |
|---|---|
| F-1 | A/B 输出 `zh→en: SAME=106 DIFF=0` 且 `en→zh: SAME=60 DIFF=0` |
| F-2 | 3 次启动，「模型初始化」**中位 ≤ 12.0s**，且最差一次 **≤ 14.0s**（基线中位 15.3s；CT2 首次开销本身在 2–6.5s 间波动，故看中位不看单次） |
| F-3 | 3 次启动，`[load] sensevoice 2pass 就绪` 到 `[load] 加载 MT 模型…` 之间静默 **≤ 1.5s** |
| F-4 | `pytest` ≥ 144 passed |
| F-5 | 一次 dualsim 跑通（`cache/soak/soak_10min.wav`），`[utt]` 行数与改动前同量级，无异常退出 |

**F-2 只认「模型初始化」，不看「进程总」**——后者被环境噪声主导（见 0.2），不作为判据。

---

## 任务 G｜CT2 加载前加一行提示

这 2–6.5 秒不可能消除，但可以让它**有交代**。这是"启动体验"的正解：
不是把每段压到 1.5 秒，而是每段都有输出。

`app/translate.py` 的 `_init_ct2()`，在**第一个** `ctranslate2.Translator(...)` 之前
（`t0` 之后）加一行：

```python
print("[load] 加载 MT 模型（2×78MB，首次约 5s）…", flush=True)
```

要求：

- 只打**一次**，不要两个模型各打一行
- 位置在两个 tokenizer 加载之后、第一个 `Translator` 之前
- 不要改 `[load] MT 中↔英 就绪` 那行的格式

**F-3 阈值的实测依据**（不用你再算）：从 `sensevoice 就绪` 到这行之间，只有
`import ctranslate2`（0.20s）+ 4 个 spm 加载（实测 0.21–0.23s）≈ **0.4s**，
所以 1.5s 的闸门有 3 倍余量。如果实测超过 1.5s，说明有别的东西被塞进来了，先报数字别改判据。

---

## 任务 I｜落盘契约测试（上一轮未做，本轮补上）

`1b70852` 改的是「行数契约」，但 `tests/` 里没有任何 `HarvestWriter` / `SessionRecorder` 测试。

### `tests/test_harvest_persist.py`

用 `tmp_path` + `types.SimpleNamespace` 造 stats（字段见 `app/harvest.py:49` 的 `row_from_stats`）。

| 用例 | 断言 |
|---|---|
| I-1 行数守恒 | 连续 `emit_from_stats` 94 条（src 各不同）→ `close()` → 文件恰好 94 行，`src` 集合与输入一致，无重复 |
| I-2 延迟回填 | 第 1 条 stats 的 `latency_device_ttfa_ms=None`；挂起后把**同一个对象**改成 `673.0`；`close()` → 该行落盘值为 `673.0` |
| I-3 空 src 不写 | `emit_from_stats(SimpleNamespace(source="   "))` → `close()` → 0 行 |

### `tests/test_live_persist.py`

`SessionRecorder(session_dir=tmp_path/"s1", mode="vad")`。

| 用例 | 断言 |
|---|---|
| I-4 live 行数 | `emit_live` 3 条 → `close()` → `live.jsonl` 恰好 3 行，`src` 顺序一致 |
| I-5 尾行回填 | 最后一条的 `latency_device_ttfa_ms` 在 `close()` 前被修改 → 落盘看到新值 |

**约束**：不许 sleep 超过 0.2s，不许起麦克风，不许连真实 TTS。

---

## 禁止（红线，一项都不要碰）

- `VAD_TEN_THRESHOLD` / `VAD_SILENCE_SEC` / `VAD_SPEECH_RMS_MIN`
- 25s 块长、`ASR_LOOP_*`、`ASR_SPEC_*`、`prefer_2pass`
- TM（`app/mt_tm.py`）与 postedit（`app/mt_postedit.py`）任何规则
- MT 解码参数（`MT_DECODE_*` / `MT_BEAM_*` / `MT_NO_REPEAT_*` / `MT_REPETITION_*`）
- 字幕、README、start.bat
- `models/` 下任何文件（不删、不转、不重建）
- `_init_transformers()` / `_translate_transformers()`
- 把 `transformers` 从 `requirements.txt` 删掉（训练与 bench 脚本仍用）
- **不要为了凑闸门改判据**：F-2 只认「模型初始化」，不许换成「进程总」或「取最好一次」

---

## 报告格式（照这个贴）

```
## F：tokenizer A/B
（脚本原始输出两行）

## F：3 次启动（原文）
BOOT 1: [boot] 依赖导入 X.Xs | 模型初始化 Y.Ys | 进程总 Z.Zs
        [load] paraformer … / sensevoice … / 加载 MT 模型… / MT 中↔英 就绪 …
BOOT 2 / BOOT 3 同上
模型初始化三次: a / b / c  中位: m
sensevoice→加载MT 间隔: i1 / i2 / i3
F 闸门: F-1 … F-5

## F：dualsim 跑通
（尾行原文 + [utt] 行数）

## G：CT2 提示
（贴 BOOT 1 里那几行的原文，确认只出现一次）

## I：测试
新增用例数 + pytest 尾行原文

## Commits
<hash> — <subject>   （F / G / I 分开提交，共 3 个）
```

任一闸门不过：**如实写 FAIL 并停手**，不要回退已验证有效的部分（F 的 A/B 若为 0，
即使 F-2 不过也只报数字，不许 `git revert` F），要不要回退由我判断。

---

## 建议提交信息

```
perf(mt): load CT2 tokenizers via sentencepiece, drop hidden 10s transformers import
feat(boot): announce CT2 model load before first-time init
test(record): cover harvest/live row-count and late TTFA backfill
```
