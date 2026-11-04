# CURSOR TASK: MT 质量线第一步 — 扩充 en→zh 评测集，量出真实失败率

日期：2026-11-04
前置资产：app/mt_tm.py、app/mt_postedit.py、data/mt_tm.jsonl（62 对）、data/mt_call_eval.jsonl（zh→en 22 句）、data/mt_call_eval_en_zh.jsonl（en→zh 10 句）、scripts/bench_mt_tm.py、scripts/bench_mt_en_zh_postedit.py
复制分割线以下内容给 Cursor。

---

任务：MT 质量线的第一步。不是修翻译，是**先把尺子做大**：把 en→zh 评测集从 10 句扩到 ≥50 句，量出当前管线（TM → ct2 → postedit）的真实失败率和失败类别分布，然后只修「出现 ≥3 次」的失败类别。延迟侧（ASR/VAD/TTS）一律不许碰。

## 0. 环境与规矩

- 当前目录就是真实仓库 D:\xuanjian\realtime-translate，解释器用 .venv\Scripts\python.exe。
- 基线 76 passed，改完必须 ≥76。
- 不新增依赖。
- 本项目的延迟优化已接近地板，本轮只做质量，任何改动不得增加译文路径延迟（硬门见 §4）。

## 1. 已知事实（不要重复验证）

- TM（data/mt_tm.jsonl，62 对）走精确/归一化匹配，命中耗时 ≈0ms，已在译文路径上。
- postedit（app/mt_postedit.py）是规则式修正，小样本上把 delay→拖延类错译从 4/10 修到 10/10，见 docs/mt_en_zh_postedit_ab.jsonl。
- 旗舰用例是**耳机听英文会议/通话 → 出中文语音**，所以 en→zh 是主方向，文体是口语（疑问句多、省略多、数字多），不是书面语。
- 已知风险：postedit 是字符串启发式，可能误改原本正确的句子。误改率必须显式测量，不许只看修复率。

## 2. 第一步：扩充评测集（人工写，不许机器回译）

把 data/mt_call_eval_en_zh.jsonl 从 10 句扩到 **≥50 句**。要求：

- 文体：真实通话/会议口语英文（"Can you hear me now?"、"Let's circle back on that"、"The build's been failing since Tuesday"、"I'll ping you the deck" 这类），不是书面语。
- 覆盖类（每类 ≥5 句，在 jsonl 里加 tag 字段标注）：疑问句、数字/单位、否定、口语省略/连读缩写、常见会议术语（deadline/sync/rollout 等）、含本项目领域词（delay/latency/echo/duplex 等）、长句（≥15 词）。
- **参考中文译文由你逐句写**，口语化、符合中文说话习惯（"延迟大概有多少"而非"有多长的拖延"）。禁止用任何机器翻译生成参考译文再贴回来——参考译文的权威性就是这个尺子的根基。
- 保留原有 10 句原样不动，追加在后。
- 格式与现有文件一致（先看现有行的字段再加 tag）。

## 3. 第二步：基线测量

扩展 scripts/bench_mt_en_zh_postedit.py（或新建），输出：

1. **逐句对照表**：src / raw ct2 译文 / postedit 后译文 / 参考译文 / 判定（ok/wrong/fixed/broken）。
   - 判定规则：与参考译文语义等价=ok；raw 错 postedit 修对=fixed；raw 对 postedit 改错=**broken**；都错=wrong。
   - 「语义等价」由你逐句判，不许用字符串相似度自动判——这是质量尺子，必须人脑。
2. **汇总**：按覆盖类分组统计 raw_ok / postedit_ok / fixed / broken 数量与占比；postedit 误改率 = broken / 总数。
3. **失败类别归纳**：把所有 wrong 的句子按失败原因聚类（如：名词误译、语序错误、术语直译、语气丢失、数字格式），每类给 2 个例句。
4. **耗时**：postedit 每句耗时，P50/P95。

## 4. 第三步：只修高频失败类别

- 只修「出现 ≥3 次」的失败类别，优先顺序：影响语义理解的（名词误译/否定反转）> 不自然但能懂的（语序/语气）。
- 修复手段二选一，挑便宜的：TM 增补（高频固定句式）或 postedit 规则（成类别的模式）。
- **硬门（全部满足才算完成）**：
  1. 误改率 = 0（broken 一个都不许有）；
  2. 原 22 句 zh→en 与原 10 句 en→zh 评测不回退；
  3. postedit P95 < 10ms；
  4. 修复后 en→zh 总通过率 ≥ 85%；
  5. 每修一类，必须有对应单测（raw 错句→修对；原本对的同类句→不被误改）。

## 5. 硬约束

- 不动 ASR / VAD / TTS / 端点 / 投机任何代码与配置。
- postedit 规则只增不改既有规则语义（除非某条规则被证明在误改，此时单独说明并给数据）。
- TM 增补只允许追加，不允许修改既有 62 对。
- 若某类失败靠规则修不了（需要真正理解语义），记入「规则边界」清单，不硬修。

## 6. 交付

1. 评测集 diff（≥40 句新增，含 tag）。
2. 基线与修复后两张汇总表（按覆盖类分组）。
3. 失败类别分布 + 每类处置（已修/规则边界）。
4. postedit 耗时 P50/P95。
5. 一句话结论：下一步该继续加规则/术语表，还是规则已到收益尽头、该考虑更强的 MT 手段。
