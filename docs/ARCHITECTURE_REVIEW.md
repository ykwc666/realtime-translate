# realtime-translate 架构评审与提升建议

> 评审时间：2026-11-03
> 评审对象：`D:\xuanjian\realtime-translate`（当前 HEAD，测试 24 passed）
> 方法：通读 `app/` 全部运行时代码 + `docs/` 三份优化记录 + 本机实测（Ryzen 7 4800H / 32GB / 全 CPU）
> 本文所有数字均标注来源：**[实测]** = 本次现场跑出来的；**[doc]** = 引自项目内优化记录。

---

## 0. 结论先说

这套东西的**工程质量远高于一般原型**：有冻结基线文档、有 bench 脚本、有 A/B 记录、有「明确不做」清单、有回归测试。问题不在"做得糙"，而在**三处根因**上：

1. **测的指标不是用户体感。** dualsim 不按实时节奏喂块，`utt_wall_ms` 实际是"纯计算耗时"，不是"停说→出字"的延迟。用它做优化目标，会得出与体感相反的结论。
2. **句尾关键路径上串了一次最贵的计算。** 二遍 ASR（SenseVoice 重解码）在 final 路径里串行，**实测 +88~330ms**，而它换来的主要是补末字和标点。这部分完全可以"提前算、延后判"。
3. **半双工靠"静音麦克风"绕过回声。** 代价是每次播报后有 2~4s 死区，且说话人方向靠 `detect_lang` 猜。这是产品形态问题，不是调参问题。

如果只做一件事：**投机定稿（speculative finalize）**，预计把"停说→首音"从 ~1.35s 压到 ~0.85s，且不牺牲质量。
如果做三件事：再加 **WASAPI 环回真双通道** 和 **指标口径修正**，项目性质会从"能演示"变成"能日常用"。

### 0.1 落地进度（2026-11-03 续）

已落地（代码在仓内，尺子见 `docs/paced_bench.jsonl`）：

| 项 | 状态 | 结果 |
|---|---|---|
| P0 尺子 `bench_paced.py` | **已做** | `--spec/--silence/--wav`；主指标 `latency_perceived_ms` |
| P0-1 投机定稿 | **已做** | 命中时 perceived ≈ **0.5–0.7s**；关投机对照 ≈ **1.0–1.1s** |
| P0-2 sync 100→20ms | **已做** | 与投机配套 |
| 投机超时不双重解码 | **已做** | 快照有效则 drain 同 job（`LATE_WAIT` + drain） |
| 默认 `VAD_SILENCE_SEC` | **0.45** | A/B：0.45 优于 0.60；0.35 不默认（气口） |
| MUTE pad 只留一处 | **已做** | TTS 线程不再二次 sleep |
| P1 WASAPI 环回 | **首版已做** | `LoopbackStream` + `--mode loopback|duplex`；`probe_loopback.py` 已通 |
| TTS 输出设备 | **已做** | `TTS_OUTPUT_DEVICE_NAME` + `--list-output`；与环回分设备 |
| 单实例锁 | **已做** | `cache/run.lock`（vad/loopback/duplex/ptt） |

未完成但仍有效的评审建议：TEN-VAD、死代码清理、requirements pin、TTS 可打断流水线。

---

## 1. 现在的架构

### 1.1 组件与数据流

```
麦克风 (PortAudio, 100ms/块)
   │  callback 线程 → queue(maxsize=50)
   ▼
MicStream.chunks()  ← 主线程（唯一的消费者）
   │
   ▼
DualTrackEngine.feed_chunk()          ← 主线程，同步执行整条链
   ├─ SileroVadEndpoint.feed()        512 sample 窗滑窗 + RMS 门 + latch
   ├─ AudioBuffer.append()            单句累积
   ├─ 每 0.75s（音频时间轴）:
   │     StreamingAsr.feed_stream()   真流式 Paraformer，只喂新增波形
   │        └─ StablePrefixTracker.update()
   ├─ VAD 静音 0.6s 命中 → EndpointArbiter.on_vad_candidate()
   │        └─ WAIT_PARTIAL(100ms 同步) → _judge_text() → CUT
   └─ CUT → _finalize_utt()  ★ 阻塞主线程
          ├─ finalize_stream()         流式收尾 + 1.0s 静音 pad + is_final
          │     └─ 若 _2pass：SenseVoice 全文重解码 → prefer_2pass_asr()
          ├─ Translator.translate()    CT2 Opus-MT int8, beam=1
          │     └─ postedit_zh_en()    正则兜底表
          ├─ StablePrefixTracker.take_speakable() → 切句
          └─ TtsQueue.submit()         TTS 线程: Piper 合成 → sd.play(blocking)
                 └─ wait_idle(60s)  ★ 等到播放完才返回
```

### 1.2 线程模型（实际只有 3 个线程）

| 线程 | 职责 | 备注 |
|---|---|---|
| PortAudio callback | 采集 → queue | 不碰 GIL 重活，设计正确 |
| 主线程 | VAD + ASR partial + 端点 + finalize + TTS 入队 | **所有重活串在这里** |
| TTS 线程 | Piper 合成 + 阻塞播放 | 单线程串行 |
| （条件）partial 线程 | 仅非真流式后端才异步 | 默认路径用不上 |

关键事实：**主线程在 `_finalize_utt()` 里会被阻塞 `final ASR + MT + 整段 TTS 播放`**（`app/dual_track.py:341` 的 `wait_idle(60)`）。这期间：
- 麦克风仍在回调、仍在往 `queue(maxsize=50)` 里塞（最多 5s 音频）；
- 只有等 `feed_chunk` 返回后，`pipeline.run_vad()` 才 `mute(True)+drain()`，把这段全丢掉；
- 所以"用户插话"必然丢失 —— 半双工是**由线程模型强制的**，不是策略选择。

### 1.3 真实延迟账本（最重要的部分）

**[实测]** 用 `cache/ab_weekday/tail_duosimo.wav`（5.33s，两句话）跑 `run.py --mode dualsim --no-tts`：

```
[partial 66ms] 延
[partial 81ms] 延迟大概有
[partial 80ms] 延迟大概有多少
--- 仲裁收句（cut_now）---
[ASR final 355ms samples=16000 2pass] 延迟大概有多少
[MT 61ms] '延迟大概有多少' => About how much delay is there?
[utt] ... first_stable_mt=699ms | wall=700ms
```

**这句语音时长约 2.4s，但 `wall=700ms`。** 700 < 2400，只可能是一个解释：`run_dualsim()` 是 `for chunk in chunks` **全速喂块，没有按实时节奏 sleep**（`app/pipeline.py:136-148`）。

算术核对：`66+81+80(partial) + 355(final) + 61(MT) + ~55(silero 54 块) ≈ 698ms ≈ wall`。
**结论：`utt_wall_ms` = 纯计算耗时，与"用户等了多久"无关。**

由此得到一个反直觉但重要的事实：`docs/OPTIMIZATION_LOG.md` 里那串 `wall 1059 → 834 → 920 → 684` 的优化史，本质上是在优化 **ASR/MT 的算力**，而不是体感。这解释了为什么"partial 0.5s 让 first_stable_mt 从 477→296 但 wall 反而 1144→1521"——体感更快、算力更贵。项目当时按 wall 判据把它回退了，**这个决策很可能是错的**（见 §3 P1-10）。

真实体感账本（按代码路径逐段时间相加，speech end 记为 t=0）：

| 环节 | 耗时 | 来源 |
|---|---:|---|
| VAD 静音确认 `VAD_SILENCE_SEC` | 600ms | [doc] config |
| 端点 partial sync `ENDPOINT_PARTIAL_SYNC_MS` | 100ms | [doc] config |
| final ASR（流式收尾，无二遍） | 157–165ms | **[实测]** |
| 二遍 SenseVoice（额外） | **+88~123ms（短句）/ +310~330ms（长句）** | **[实测]** |
| MT（CT2 int8, beam=1） | 61–66ms | **[实测]** |
| Piper 合成（短句） | 81–201ms | **[实测]** |
| 音频设备输出延迟 | 50–100ms | 估 |
| **合计（现状）** | **≈ 1.15–1.45s** | |

**[实测] 二遍 ASR 的收益，我做了逐句对比**（`accept_phrases.wav`，4 段）：

| # | 一遍 | 二遍 | 差异 |
|---|---|---|---|
| 0 | `延迟大概有多` | `延迟大概有多少` | **补末字** |
| 1 | `今天天气怎么样` | 同 | — |
| 2 | `现在是星期二` | 同 | — |
| 3 | `他现在没有回我的消` | `他现在没有回我的消息` | **补末字** |

也就是说：二遍 ASR 的收益 90% 是"补最后一个字"。而它正好是 `docs/BASELINE_FINAL_ONLY.md` 里说的那个致命失效模式（少末字→怪译）。**所以不能简单删掉它，而应该让它不再占用关键路径。**

### 1.4 启动开销

**[实测]**（热盘）：`ASR 4.1s + MT 11.0s + TTS 预热 14.9s`（冷盘）；整链路 `模型就绪，用时 16.6s`。
TTS 预热里 7.7s(zh) + 7.2s(en) 主要是 `PiperVoice.load()` 建 ONNX session。
→ 建议把 SenseVoice（仅二遍用）和 en 音色改成**后台惰性加载**，冷启动可降到 5s 级。

---

## 2. 三处根因（展开）

### 根因 A：指标口径失真

- `bench_dualsim.py` 只取 `results[0]`（第一句），且双轨仿真不按实时节奏喂块。
- `first_stable_mt_ms` 从"开说"计时，包含了说话时长本身。
- `utt_wall_ms` 在 `play=True` 时还包含 TTS 播放时长（`wait_idle`）。

**后果：** 你在优化的数字，和用户抱怨的"慢"不是同一个东西。文档里也承认了这点（"utt_wall 包含 VAD 静音门槛"），但没有把它从"主指标"降级。

**建议口径（三件套，别再一次算一个数）：**

```
t_cut      = 静音门槛达成（端点决策时刻）
t_asr_done = final 文本可用
t_mt_done  = 译文可用
t_first_audio = 首个音频帧真正推向声卡
latency_perceived = t_first_audio − t_speech_end      ← 唯一该用来对外的数
```

并且新增一个**按实时节奏喂块**的 harness（`bench_latency_knobs.py` 已有雏形，用它当主 bench），否则永远测不到体感。

### 根因 B：句尾关键路径串行 + 最贵的活压在最后

现状顺序：`静音等待 → sync → final ASR(with 2pass) → MT → TTS 合成 → 播放`，全串行，且"最贵的一段（二遍 ASR）在最后才开始算"。

两个正交的解法，**建议都做**：

**B-1 投机定稿（speculative finalize）** —— 把 final ASR 提前到"还不确定要不要收句"的时候算。

```
t=0     说完
t=250   静音 250ms：buffer 快照 → 后台线程用独立 stream 做 oneshot final(含2pass)  ← 提前！
t=600   静音 600ms：端点确认 CUT（此时快照结果通常已就绪，实测 final 需 470ms → 250+470=720）
t=720   直接取已算好的 final 文本 → MT 65ms → TTS 100ms
t≈885   首音
        若中途恢复说话 → 丢掉投机结果（浪费的是算力，不是体验）
```

因为流式 ASR 的 RTF 只有 **0.10**［实测：5.33s 音频 534–620ms］，浪费一次投机解码的成本极低（~500ms 单核占用），这是典型的"用算力换延迟"，和 `docs/OPTIMIZATION_ROADMAP.md` 里那条"乐观调度"原则完全一致。

顺带可以去掉 `ENDPOINT_PARTIAL_SYNC_MS=100`（final 已经覆盖 partial 的信息），再省 100ms。

**B-2 二遍 ASR 与 MT/TTS 并行 + 版本失效** —— 你已经有一半的机制（`TtsQueue.invalidate(utt_id, seq, version)`）。

```
CUT → 用流式文本立刻 MT(61ms) → 立刻 TTS(v0) 出声
    ↘ 并行跑二遍 ASR(300ms)
       若末字有变 → 重译(61ms) → submit(v1, min_version) → 未播的旧任务被丢弃
```
风险：可能出现"先播半句再改口"。因为二遍通常只差末尾 1 个字，可以定规则：**只差 ≤2 字且是尾部追加 → 不回滚，只记录**；差异更大才回滚。B-1 做完后 B-2 的收益会被吃掉大半，两条建议**选 B-1 为主**。

### 根因 C：半双工靠静音麦绕过回声

现状的死区（每次播报后）：

| 段 | 时长 | 位置 |
|---|---:|---|
| TTS 合成 + 播放 | 1.3–2.7s | `wait_idle` 阻塞主线程 |
| `PLAYBACK_MUTE_PAD_SEC` | 500ms | `tts_queue.py:152-156` |
| 又一份 `PLAYBACK_MUTE_PAD_SEC` | 500ms | `pipeline.py:124-130` |
| **合计** | **2.3–3.7s 不可插话** | |

注意 pad 被**扣了两次**（TTS 线程一次、`run_vad` 再一次），这是明确的冗余。

**架构级解法：WASAPI 环回（loopback）采集对端音频。**

- 你的麦克风 = 你说的话 → 固定 zh→en
- 系统输出环回 = 对方说的话 → 固定 en→zh
- 两者物理隔离，**不存在回声**，也就不需要静音麦、不需要半双工
- 方向由通道决定，`detect_lang()` 那套字符比例猜法可以整体退役
- 可以双向同时工作，还能做 barge-in

**[实测] 注意：`sounddevice 0.5.6` 不支持 loopback**（`WasapiSettings` 只有 `exclusive/auto_convert/explicit_sample_format`）。需要换/加依赖：
- `soundcard`（`sc.get_microphone(id=..., include_loopback=True)`，最省事）
- 或 `PyAudioWPatch`（WASAPI loopback 的 pyaudio fork）

配套：TTS 要真正"说给对方听"，还得输出到 **虚拟声卡**（VB-CABLE / VoiceMeeter），否则现在只是"自娱自乐式跟读"。这是产品化必须补的一环。

---

## 3. 提升清单（按 ROI 排序）

改动量：S ≈ 半天内、M ≈ 1–2 天、L ≈ 3 天以上。

### P0（先做这三件，预计体感 −400~500ms）

| # | 项 | 现状 | 怎么做 | 预期 | 风险 | 改动量 |
|---|---|---|---|---|---|---|
| P0-1 | **投机定稿** | final ASR 在 CUT 之后才开算 | 静音达 `SPEC_CUT_MS≈250ms` 时对 `AudioBuffer` 快照，在 worker 线程用**独立 `create_stream()`** 跑 `oneshot + 2pass`；CUT 时取结果，未就绪则等 | −300~350ms | 恢复说话时浪费算力；快照与实时流文本不一致（用 `prefer_final_asr` 兜） | M |
| P0-2 | **去掉端点 sync 等待** | 固定 100ms | P0-1 就绪后此等待无意义，直接 `_judge_text` | −100ms | 无 | S |
| P0-3 | **finalize 异步化 + 音频线程不阻塞** | `wait_idle(60)` 在主线程 | 拆出 `FinalizeWorker` 线程；主线程只负责"喂块 + 状态迁移"；播放与采集解耦 | 不丢音、可插话、`utt_wall` 不再含播放时长 | 状态机要重写，易引入竞态 | L |

> P0-1 的骨架（可直接贴到 `dual_track.py`）：
> ```python
> # CUT 之前，静音达到投机阈值时：
> def _maybe_speculate(self):
>     if self._spec is not None or not self.buf.size: return
>     snap = self.buf.copy()
>     self._spec = {"audio": snap, "done": threading.Event(), "res": None}
>     def run():
>         st = AsrIncState()               # 独立 state，不影响实时流
>         self._spec["res"] = self.asr.transcribe_with_2pass(snap)
>         self._spec["done"].set()
>     threading.Thread(target=run, daemon=True).start()
> # CUT 时：
> res = None
> if self._spec and self._spec["done"].wait(0.25):
>     res = self._spec["res"]
> else:
>     res = self.asr.finalize_stream(audio, self._asr_state)   # 回退到原路径
> ```

### P1（体验与质量跃迁）

| # | 项 | 说明 | 预期 | 改动量 |
|---|---|---|---|---|
| P1-1 | **WASAPI 环回真双通道** | 见 §2 根因 C。新增 `audio_io.LoopbackStream`，与 `MicStream` 对称；两条 `DualTrackEngine` 实例互不干扰 | 消灭回声 / 免 `detect_lang` / 全双工 | L |
| P1-2 | **TTS 流水线化 + 可打断** | 当前 `synthesize()` 把整句攒完才 `sd.play(blocking=True)`，且用全局 stream（无法 `sd.stop()` 打断）。改成：① 子句级预取（合成 N+1 与播放 N 重叠）；② 自建 `sd.OutputStream` 回调写 ring buffer，可即时停 | 长句首音 −100~200ms，延迟可控 | M |
| P1-3 | **端点判定去白名单化** | 「星期」白名单是打补丁（`mt_policy.py:218-230`），永远覆盖不了长尾。用**在线标点/句法完整度**替代：sherpa-onnx 自带 `OnlinePunctuation`（CT-Transformer zh-en），~几 ms，问句/句尾判定比手写虚词表稳得多 | 少一个永久维护负担；「星期」类特殊逻辑消失 | M |
| P1-4 | **VAD 换 TEN-VAD** | sherpa-onnx 2.0.1 已内置 `TenVadModelConfig`；模型 `ten-vad.onnx`(324KB) / `int8`(126KB)。TEN-VAD 专为流式低延迟设计，可支撑更短静音门槛。你已有 `VAD_BACKEND` 抽象，**加一个分支即可 A/B** | 静音门槛 600→450ms 而不吞字（需实测） | S |
| P1-5 | **ASR 换双语流式模型** | `sherpa-onnx-streaming-paraformer-bilingual-zh-en`（同 API `from_paraformer`，可直接替换）或 `streaming-zipformer-bilingual-zh-en`。收益：**中英混说直接识别**，`detect_lang()` 退役，专名（达摩院类）跨语种更稳 | 质量 + 少一处猜测逻辑 | S~M |
| P1-6 | **麦克风侧加降噪** | sherpa-onnx 2.0.1 内置 `OnlineSpeechDenoiser`（GTCRN/DPDFNet）。笔记本麦在风扇/键盘噪声下 WER 明显受损，GTCRN 开销约 1ms/frame | WER 改善（需实测），间接减少"末字丢" | S |
| P1-7 | **术语表 + 翻译记忆(TM)** | Opus-MT 无上下文，同一专名每次译法不同。加：① 术语表（源→目标强制替换，后编辑阶段，0ms）；② 精确 TM 命中（"你好/谢谢/明天见"这类高频句，命中即 0ms 返回）；③ 通话内滚动 2 句上下文拼接（仅当 MT 延迟有余量时开） | 一致性 + 高频句零延迟 | M |
| P1-8 | **MT 参数扫一遍** | 现在 `beam_size=1, max_decoding_length=128, intra_threads=4` 是拍死的（`translate.py:109-113`）。`intra_threads` 在 4800H 上 2/4/6 值得扫；`beam_size=2` 质量换 ~2× 延迟也值得量一次 | 未知，可能白捡 10–20ms | S |
| P1-9 | **质量评测集扩容** | `data/mt_call_eval.jsonl` 只有 22 条。既然有 `bench_mt_call_quality.py`，把评测集扩到 150–300 条（含专名、否定、时态、反问），否则 LoRA/术语表的效果都不可信 | 后续所有质量改动的判据 | S（体力活） | 
| P1-10 | **重开"中途译"作为可选模式** | 文档已按 wall 判据否决。但在**长句/独白**场景，先用流式文本在说话中就译、边说边播，体感远好于"说完等 1.3s"。建议做成 `--mode sim`，与 `final_only` 并存，各配各的指标（sim 看 `time_to_first_audio_during_speech`） | 长句体感质变 | M |

### P2（工程化，决定这个项目能活多久）

| # | 项 | 现状 | 建议 |
|---|---|---|---|
| P2-1 | **没有版本控制** | `git` 仓库根在 `D:\xuanjian`，**0 个文件被跟踪**；`realtime-translate` 无 `.gitignore` | 给项目单独建 repo；`.gitignore` 掉 `models/ cache/ .venv*/ .pytest_cache/`；把 `docs/bench_history.jsonl` 纳入跟踪——这是这个项目最值钱的资产 |
| P2-2 | **配置是模块级常量** | `config.py` 128 行硬编码，A/B 靠 `bench_*` 里 `config.ASR_BACKEND = ...` 猴子补丁 | `pydantic-settings` + `config.yaml` + `RT_*` 环境变量 + `--set k=v`；config 变成不可变 dataclass。否则每个实验都要改源码 |
| P2-3 | **配置腐烂** | 至少 6 个开关已无效或被注释为 legacy：`MID_MT_ENABLED`（**无任何读取点**）、`MIN_MT_CHARS`、`MT_INFO_MIN`、`MT_POLICY`、`MT_PENDING_MAX_WAIT_SEC`、`VAD_HANG_EXTRA_SILENCE_SEC` | 无效的删掉；有效的集中到一个 `Settings` 并写"谁读它" |
| P2-4 | **死代码** | `mt_policy.py` 357 行里，`should_translate()`（130 行 REINA 逻辑）**零调用**；`completeness_tier`/`is_strong_hang`/`strip_strong_hang_suffix` 只有测试在用；`vad_stream.py` 的 `_silence_hang`/`_hang_hold`/`set_hang_hold()` 无人调用 | 删。测试跟着删（测死代码是负资产） |
| P2-5 | **日志** | 全项目 `print`，无 `logging`，bench 靠人眼+人工抄进 md | `logging` + 结构化 JSON 事件；bench 直接消费 JSONL。现在**指标靠人工记账，极易漏** |
| P2-6 | **依赖未锁定** | `requirements.txt` 全无版本号，且含 `torch / torchaudio / funasr / modelscope`——**运行时不 import 任何一个**（`funasr` 只在 `download_models.py`，`torch` 只在 `train_opus_lora_prefix.py`） | 拆 `requirements.txt`（运行时，全部 pin）+ `requirements-dev.txt` + `requirements-legacy.txt`。**删掉 torch/funasr/modelscope 能省 ~2.5GB 和若干启动时间**。你们已经踩过 ORT 1.24/1.28/1.31 版本差异导致 DML 崩溃的坑，pin 是刚需 |
| P2-7 | **单实例** | 文档写"同时只能一个进程"，代码里没有任何防护 | `msvcrt.locking` 或 `filelock` + 明确报错 |
| P2-8 | **异常恢复** | `run_vad` 只有 `try/finally`，任一模型调用抛异常 → 整场通话挂掉 | 每句 `try/except`，异常只丢当前句并打日志 |
| P2-9 | **测试覆盖严重偏斜** | 24 个测试全在 `mt_policy`/`endpoint`/`postedit`。**最脏的字符串启发式零测试**：`prefer_2pass_asr`(asr_stream.py:53)、`merge_overlap_text`(:80)、`untranslated_tail`(stable_prefix.py:27)、`take_speakable`、`TtsQueue` 的失效语义 | 这几个都是纯函数，补参数化单测成本极低、收益极高（它们出 bug 的表现是"偶发怪译"，最难查） |
| P2-10 | **无静态检查** | 无 `pyproject.toml` / ruff / mypy | 加 ruff（E,F,I,UP,B）+ pyright basic。`dual_track.py` 的 6 层嵌套和 12 个 `self._xxx` 可变状态，靠类型检查能挡掉一部分 |

---

## 4. 代码级问题清单（具体到行）

| 位置 | 问题 | 建议 |
|---|---|---|
| `dual_track.py:341` | `tts_q.wait_idle(60)` 在音频消费主线程里等播放结束 | P0-3 |
| `dual_track.py:292-294` | `while self._partial_busy: sleep(0.02)` 忙等最多 2s | 换 `threading.Event` |
| `dual_track.py:242-252` | `_last_partial_text` 被 partial 线程写、主线程读，无锁 | 用 `_lock` 或 `queue` 传递 |
| `dual_track.py:436-495` | 两个几乎相同的"丢弃空录音"分支（443-447 / 476-482）；`chunk_f` 转换重复两次 | 抽 `_abort_empty_utt()`；嵌套 6 层建议重写成显式状态机 `IDLE/SPEECH/HOLDING/FINALIZING` |
| `pipeline.py:124-130` + `tts_queue.py:150-158` | `PLAYBACK_MUTE_PAD_SEC` 被扣两次，每次播报后多死 500ms | 只留一处（建议留在采集侧，TTS 线程只管播） |
| `tts_queue.py:150-158` | `finally` 里 `mute_cb(True)` 紧跟 `mute_cb(False)`，语义混乱（真正起作用的是 play 前那次 `True`） | 改写成 `mute(True) → play → sleep(pad) → mute(False)` |
| `tts_queue.py:48-49` | `_min_seq/_min_version` 按 `utt_id` 累加，长通话无界增长 | 只保留最近 2 个 utt |
| `vad_stream.py:214,239,243` | `np.concatenate` 每个 512 窗都追加，单句 O(n²) 拷贝（15s 上限 ≈ 469 次） | 换 `AudioBuffer`（你已经为 ASR 写好了） |
| `vad_stream.py:132-141,150-158` | `_silence_hang` / `_hang_hold` / `set_hang_hold()` 是 hang 断句遗留，已废弃仍占位 | 删（`VAD_HANG_EXTRA_SILENCE_SEC` 一起删） |
| `vad_stream.py:224-231` | RMS 门用 `VAD_SPEECH_RMS_MIN=0.035` 硬门，轻声/远场容易漏 | 换 TEN-VAD 后此门可放宽；或改用 VAD 概率 + 自适应噪声地板 |
| `stable_prefix.py:176-194` | `parts` 由 `masked` 切分，但 `complete` 又用 `buf.strip()` 判断，两套字符串不一致；缩写掩码把 `X.` 换成 `X\0` 改变了长度 | 统一在 `masked` 上判断；补单测（`p.m.` / `U.S.` / `Mr.`） |
| `asr_stream.py:53-77` | `prefer_2pass_asr` 的 6 条分支全是经验阈值（`common>=max(2,len/2)`、`len+2`），无测试 | 补参数化单测，把阈值变成命名常量 |
| `asr_stream.py:80-127` | `merge_overlap_text` 同样是无测试的启发式（默认后端已不走这条路） | 若保留 SenseVoice 回退就补测；否则删 |
| `translate.py:107-115` | `max_length=256` 截断静默发生；`beam_size` 写死 | 溢出时打 WARN（长句被腰斩会导致"译一半"且无人知晓） |
| `translate.py:45` | warmup 只译 `"你好"`（zh→en），en→zh 的 CT2 首调未预热 | 补一条 `"hello"` |
| `pipeline.py:51` | `MIN_SPEECH_SEC` 过滤放在 `process_audio`，而 `run_vad` 路径走的是 `endpoint.min_speech`，两处阈值各自为政 | 收敛到一处 |
| `config.py:100-108` | 6 个 legacy 开关无读取点或仅被死代码读 | 见 P2-3 |
| `requirements.txt` | 4 个未使用重依赖，全部无 pin | 见 P2-6 |

---

## 5. 建议的落地顺序

**第 1 步：把尺子修对（不动产品代码）**
1. 新增 `scripts/bench_paced.py`：按实时节奏喂块 + 打 `t_cut/t_asr/t_mt/t_first_audio` 四个时间戳，输出 JSONL。
2. 在 `DualTrackStats` 里加这四个字段（现在只有起点/终点，中间是黑洞）。
3. 用当前代码跑一次，得到"现状体感基线"——**这个数才是后续所有决策的依据**。

**第 2 步：投机定稿（P0-1 + P0-2）**
- 加 `SPEC_CUT_MS`，独立 stream 做 oneshot，CUT 时优先取投机结果。
- 验收：`latency_perceived` 应从 ~1.35s 降到 ~0.85s；转写质量不变（用 `accept_offline.py` + 现有 checklist 回归）。

**第 3 步：清理与锁死（P2-1/2/4/6）**
- 建独立 repo + `.gitignore`；删死代码；`requirements` 拆分并 pin。
- 这一步没有性能收益，但它决定第 4 步之后还能不能"回头"。

**第 4 步：真双通道（P1-1）**
- `LoopbackStream` + 双 `DualTrackEngine`；先只做"对端→我"单向，验证 echo 消失后再开双向。

**第 5 步：模型换代（P1-4 / P1-5 / P1-6）**
- 每换一个都用 `bench_paced.py` 出对照表；VAD 与 ASR 不要同版本换（否则归因不了）。

---

## 6. 不建议做的事（避免重复踩坑）

- **不要把 MT 换成本地 LLM 进首句关键路径。** 4800H 无 GPU，q4 的 1.7B 模型解一句 20 token 也要 300ms+，比 Opus-MT 的 61ms 差一个量级。质量档可以另开"说完再精译"的慢路径（用户手动触发）。
- **不要再试 DirectML。** 项目已实测 3 次（Piper / SenseVoice / ORT API 冲突），Renoir 上全面更慢，ORT 版本不兼容。当前 `onnxruntime 1.31.1` 是 Release 构建、自动 ISA dispatch，已经吃到 AVX2 了。
- **不要用 partial 更密（0.5s）来"提快感"。** 算力翻倍、碎译变多。真要提前，就上 P1-10 的 sim 模式，而不是加密 partial。
- **不要继续扩「星期」这类白名单。** 那是补丁不是机制，每加一条都增加回归面（现在 `VAD_FORCE_END_UNCHANGED_WEEKDAY` 就有 4/6 两套阈值在打架）。
- **不要在没有 paced bench 的情况下动 VAD 门槛。** 过去几轮 wall 的结论都可能是口径错误导致的误判。

---

## 7. 附：本次实测原始数据

环境：Python 3.12.0 / numpy 2.5.4 / sherpa-onnx 2.0.1 / onnxruntime 1.31.1 / ctranslate2 4.8.4 / sounddevice 0.5.6 / torch 2.15.0+cpu

**A. dualsim 整链（`tail_duosimo.wav`, `--no-tts`）**

| utt | partials | ASR final | MT | wall(=纯计算) |
|---|---|---|---|---|
| 1 | 3（66/81/80ms） | 355ms(2pass) | 61ms | 700ms |
| 2 | 3（66/90/79ms） | 331ms(2pass) | 66ms | 695ms |

**B. 二遍 ASR 开销（`finalize_stream` 计时，含流式收尾）**

| 片段 | 一遍 | 二遍 | 差值 |
|---|---:|---:|---:|
| tail_duosimo 整段 | 157ms | 474ms | +317ms |
| tail_duosimo 整段（复测） | 165ms | 501ms | +336ms |
| accept_phrases #0 | 191ms | 279ms | +88ms |
| accept_phrases #1 | 123ms | 236ms | +113ms |
| accept_phrases #2 | 191ms | 275ms | +84ms |
| accept_phrases #3 | 172ms | 295ms | +123ms |

**C. 流式 ASR 吞吐**：5.33s 音频分 34 块喂入 = 534–620ms → **RTF ≈ 0.10**

**D. Piper 合成**（每句只 yield 1 个 chunk，说明"分块流式播放"对短句无效，只有"子句级预取"有效）

| 文本 | 首 chunk | 总合成 | 音频时长 |
|---|---:|---:|---:|
| `About how much delay is there?` | 109ms | 109ms | 1.81s |
| `How's the weather today?` | 81ms | 81ms | 1.31s |
| `这个项目的延迟大概有多少毫秒呢` | 201ms | 201ms | 2.75s |
| `今天天气怎么样` | 133ms | 133ms | 1.66s |

**E. 启动分解**（冷盘）：import 模块 1.1s / ASR 4.1s / MT 11.0s / TTS 预热 14.9s（zh 7.7 + en 7.2）

**F. WASAPI loopback 可用性**：`sounddevice 0.5.6` 的 `WasapiSettings` **无 `loopback` 参数**（`TypeError`），需改用 `soundcard` 或 `PyAudioWPatch`。

**G. 回归**：`pytest tests -q` → **24 passed in 1.09s**
