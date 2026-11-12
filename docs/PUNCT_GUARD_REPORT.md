# PUNCT_GUARD 验收报告

基线：`c6e6690`。只改展示层回退；ASR/MT/标点模型未动。

## 027 对照

```
old src: 些葱，然后把这个嗯土豆丝儿放下去。要不断的翻炒。
new src: 些葱，然后把这个嗯土豆丝儿放下去，要不断的翻炒。呃，放下去以后呢
```

`asr_err`：0.5217 → **0.3043**；`punct_fallback=true`。

## 报数（A–H）

```
A pytest:        177 passed
B KPI 变化:      Test zh_en asr_err_mean 0.2365→0.2329 / asr_ok 50→51；Dev zh_en 不变；Test en_zh asr_err_mean 噪声回落到 0.2309
C mt_ok:         Test zh_en 47 / Dev zh_en 47 / Test en_zh 53 / Dev en_zh 45
D 展示变短:      0 条
E punct_fallback:1 条（Test 027）；punct_dropped 1 条
F pick 分布:     Test zh_en {offline:71, online:1, merge:1}；210 条里对 210 / 错 0 / 例外 0
G 日志:          guard 1 次，drop 1 次
H git diff:      asr_stream 0 行，config 0 行
```

### C 说明

期望 Dev en_zh `mt_ok=46`；本跑 **45**。Dev 无 `punct_fallback`，属 §5 写明的 en_zh 噪声地板（±1），**未改代码凑数**。其余三集 `mt_ok` 与期望一致。
