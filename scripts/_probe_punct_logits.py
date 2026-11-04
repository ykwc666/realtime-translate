"""Probe last-token punct logits for completeness signal."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "sherpa-punct-ct-zh-en" / "model.int8.onnx"


def main() -> None:
    sess = ort.InferenceSession(
        str(MODEL), providers=["CPUExecutionProvider"]
    )
    meta = sess.get_modelmeta().custom_metadata_map
    tokens = meta["tokens"].split("|")
    punctuations = meta["punctuations"].split("|")
    tok2id = {t: i for i, t in enumerate(tokens)}
    unk = tok2id.get("<unk>", len(tokens) - 1)

    def scores(text: str):
        ids = np.array([[tok2id.get(ch, unk) for ch in text]], np.int32)
        lens = np.array([len(text)], np.int32)
        logits = sess.run(None, {"inputs": ids, "text_lengths": lens})[0][
            0, len(text) - 1
        ]
        e = np.exp(logits - logits.max())
        pr = e / e.sum()
        # 0 unk, 1 _, 2 ，, 3 。, 4 ？, 5 、
        term = float(pr[3] + pr[4])
        soft = float(pr[1] + pr[2] + pr[5])
        return term, soft, punctuations[int(logits.argmax())], pr

    cases = [
        ("incomplete", "现在是星"),
        ("incomplete", "现在是星期"),
        ("incomplete", "这是星期"),
        ("complete", "现在是星期二"),
        ("complete", "现在是星期三"),
        ("complete", "延迟大概有多少"),
        ("complete", "今天天气怎么样"),
        ("complete", "你好"),
        ("hang", "延迟大概有多"),
        ("hang", "今天天气怎么"),
        ("hang", "感觉你翻译的有"),
        ("hang", "他现在没有回我的"),
        ("complete", "他现在没有回我的消息"),
        ("complete", "明星"),
        ("complete", "五十八"),
    ]
    print(f"{'label':12} {'term':6} {'soft':6} {'arg':2} text")
    for lab, t in cases:
        term, soft, arg, _pr = scores(t)
        print(f"{lab:12} {term:6.3f} {soft:6.3f} {arg:2} {t}")


if __name__ == "__main__":
    main()
