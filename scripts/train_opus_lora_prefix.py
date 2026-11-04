"""CPU LoRA: truncated ZH prefix -> full EN on local Opus-MT."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch  # noqa: E402
from peft import LoraConfig, TaskType, get_peft_model  # noqa: E402
from torch.utils.data import DataLoader, Dataset  # noqa: E402
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # noqa: E402


class PrefixMtDataset(Dataset):
    def __init__(self, path: Path, tokenizer, max_len: int = 128) -> None:
        self.rows = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.rows.append(json.loads(line))
        self.tok = tokenizer
        self.max_len = max_len

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> dict:
        row = self.rows[idx]
        enc = self.tok(
            row["zh"],
            text_target=row["en"],
            truncation=True,
            max_length=self.max_len,
            padding="max_length",
            return_tensors="pt",
        )
        labels = enc["labels"].squeeze(0)
        labels[labels == self.tok.pad_token_id] = -100
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "labels": labels,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(ROOT / "data" / "prefix_mt_train.jsonl"))
    parser.add_argument("--base", default=str(ROOT / "models" / "opus-mt-zh-en"))
    parser.add_argument("--out", default=str(ROOT / "models" / "opus-mt-zh-en-lora-prefix"))
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--r", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=0, help="0 = full epochs")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print("missing data, run scripts/build_prefix_mt_data.py first")
        return 2

    device = torch.device("cpu")
    tok = AutoTokenizer.from_pretrained(args.base)
    base = AutoModelForSeq2SeqLM.from_pretrained(args.base)
    lora = LoraConfig(
        task_type=TaskType.SEQ_2_SEQ_LM,
        r=args.r,
        lora_alpha=16,
        lora_dropout=0.05,
        target_modules=["q_proj", "v_proj", "k_proj", "out_proj"],
    )
    model = get_peft_model(base, lora)
    model.print_trainable_parameters()
    model.to(device)
    model.train()

    ds = PrefixMtDataset(data_path, tok)
    loader = DataLoader(ds, batch_size=args.batch, shuffle=True)

    opt = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=args.lr)
    step = 0
    for epoch in range(args.epochs):
        total = 0.0
        n = 0
        for batch in loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            out = model(**batch)
            loss = out.loss
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += float(loss.item())
            n += 1
            step += 1
            if step % 20 == 0:
                print(f"epoch={epoch+1} step={step} loss={loss.item():.4f}")
            if args.max_steps and step >= args.max_steps:
                break
        print(f"epoch={epoch+1} mean_loss={total / max(1, n):.4f}")
        if args.max_steps and step >= args.max_steps:
            break

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_dir))
    tok.save_pretrained(str(out_dir))
    meta = {
        "base": args.base,
        "epochs": args.epochs,
        "r": args.r,
        "steps": step,
        "data": str(data_path),
        "n_train": len(ds),
    }
    (out_dir / "train_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("LORA_OK", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
