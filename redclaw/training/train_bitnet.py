"""Fine-tune BitNet b1.58 2B-4T with STE-aware LoRA on RedClaw data.

Loads the BF16 safetensors model from HuggingFace, injects LoRA adapters,
trains on instruction-following data, and saves the LoRA weights.

Usage:
    python -m redclaw.training.train_bitnet \
        --base-model ./models/BitNet-2B \
        --data-dir training_data/bitnet/ \
        --output-dir training_data/bitnet_finetuned/ \
        --epochs 3 \
        --batch-size 4 \
        --lr 2e-4 \
        --rank 8

Requirements:
    - PyTorch with CUDA
    - transformers, safetensors packages
    - BitNet BF16 weights from HuggingFace:
      huggingface-cli download microsoft/bitnet-b1.58-2B-4T-bf16 --local-dir ./models/BitNet-2B
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import tempfile
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import Dataset, random_split

from redclaw.training.bitnet_lora import (
    BitNetLoRAConfig,
    inject_lora,
    extract_lora_state_dict,
)

logger = logging.getLogger(__name__)


class InstructionDataset(Dataset):
    """Alpaca-format instruction-following dataset."""

    def __init__(
        self,
        data: list[dict],
        tokenizer: object,
        max_length: int = 512,
    ) -> None:
        self.data = data
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        item = self.data[idx]
        instruction = item.get("instruction", "")
        inp = item.get("input", "")
        output = item.get("output", "")

        # Format as Alpaca prompt
        if inp:
            prompt = f"### Instruction:\n{instruction}\n\n### Input:\n{inp}\n\n### Response:\n"
        else:
            prompt = f"### Instruction:\n{instruction}\n\n### Response:\n"

        full_text = prompt + output

        # Tokenize
        encodings = self.tokenizer(
            full_text,
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )

        input_ids = encodings["input_ids"].squeeze(0)
        attention_mask = encodings["attention_mask"].squeeze(0)

        # Labels: same as input_ids but mask the prompt portion
        labels = input_ids.clone()
        prompt_ids = self.tokenizer(
            prompt,
            truncation=True,
            max_length=self.max_length,
            padding=False,
            return_tensors="pt",
        )
        prompt_len = prompt_ids["input_ids"].shape[-1]
        labels[:prompt_len] = -100  # Ignore prompt in loss

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }


def _load_base_model(model_path: Path) -> tuple[nn.Module, object]:
    """Load BitNet b1.58 2B model from safetensors.

    Uses transformers library to load the BF16 model.
    Falls back to a simple Linear stack if transformers not available.
    """
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        logger.info("Loading BitNet model from %s", model_path)
        model = AutoModelForCausalLM.from_pretrained(
            str(model_path),
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        )
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        return model, tokenizer
    except ImportError:
        raise ImportError(
            "transformers package required for BitNet training. "
            "Install with: pip install transformers safetensors"
        )


def _get_cosine_schedule(
    optimizer: torch.optim.Optimizer,
    warmup_steps: int,
    total_steps: int,
) -> callable:
    """Create a cosine learning rate schedule with warmup."""
    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return step / max(warmup_steps, 1)
        progress = (step - warmup_steps) / max(total_steps - warmup_steps, 1)
        return 0.5 * (1.0 + math.cos(math.pi * progress))
    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)


def train_bitnet_lora(
    base_model_path: Path,
    data_dir: Path,
    output_dir: Path,
    epochs: int = 3,
    batch_size: int = 4,
    lr: float = 2e-4,
    rank: int = 8,
    alpha: float = 16.0,
    warmup_steps: int = 100,
    max_length: int = 512,
    eval_every: int = 500,
    seed: int = 42,
) -> dict:
    """Fine-tune BitNet with STE-aware LoRA.

    Returns training metrics dict.
    """
    torch.manual_seed(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Device: %s", device)

    if device.type == "cpu":
        logger.warning("Training on CPU — this will be very slow. GPU recommended.")

    # ── Load model ──
    model, tokenizer = _load_base_model(base_model_path)
    logger.info("Base model loaded: %d parameters", sum(p.numel() for p in model.parameters()))

    # ── Inject LoRA ──
    lora_config = BitNetLoRAConfig(rank=rank, alpha=alpha)
    model = inject_lora(model, lora_config)
    model.to(device)

    # Count trainable params
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    logger.info("Trainable: %d / %d (%.2f%%)", trainable, total, (trainable / total) * 100)

    # ── Load data ──
    train_path = data_dir / "train.json"
    val_path = data_dir / "val.json"

    if not train_path.is_file():
        raise FileNotFoundError(f"No train.json in {data_dir}. Run prepare_bitnet_data first.")

    with open(train_path, encoding="utf-8") as f:
        train_data = json.load(f)
    val_data = []
    if val_path.is_file():
        with open(val_path, encoding="utf-8") as f:
            val_data = json.load(f)

    logger.info("Data: %d train, %d val", len(train_data), len(val_data))

    train_dataset = InstructionDataset(train_data, tokenizer, max_length)
    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=batch_size, shuffle=True, drop_last=False,
    )

    val_loader = None
    if val_data:
        val_dataset = InstructionDataset(val_data, tokenizer, max_length)
        val_loader = torch.utils.data.DataLoader(val_dataset, batch_size=batch_size)

    # ── Optimizer ──
    trainable_params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable_params, lr=lr, weight_decay=0.01)
    total_steps = len(train_loader) * epochs
    scheduler = _get_cosine_schedule(optimizer, warmup_steps, total_steps)

    # ── Training loop ──
    best_loss = float("inf")
    history: list[dict] = []
    start_time = time.time()

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        num_batches = 0

        for step, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
            )
            loss = outputs.loss

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(trainable_params, 1.0)
            optimizer.step()
            scheduler.step()

            epoch_loss += loss.item()
            num_batches += 1

            # Logging
            if (step + 1) % 50 == 0:
                avg_loss = epoch_loss / num_batches
                lr_current = scheduler.get_last_lr()[0]
                elapsed = time.time() - start_time
                logger.info(
                    "Epoch %d/%d Step %d/%d: loss=%.4f lr=%.6f (%.0fs)",
                    epoch + 1, epochs, step + 1, len(train_loader),
                    avg_loss, lr_current, elapsed,
                )

            # Eval checkpoint
            if eval_every > 0 and (step + 1) % eval_every == 0 and val_loader:
                val_loss = _evaluate(model, val_loader, device)
                if val_loss < best_loss:
                    best_loss = val_loss
                    _save_lora(model, output_dir / "lora_best.pt")
                logger.info("Eval: val_loss=%.4f (best=%.4f)", val_loss, best_loss)

        avg_epoch_loss = epoch_loss / max(num_batches, 1)
        logger.info("Epoch %d complete: avg_loss=%.4f", epoch + 1, avg_epoch_loss)

        history.append({
            "epoch": epoch + 1,
            "train_loss": round(avg_epoch_loss, 4),
        })

    # ── Save final LoRA weights ──
    lora_path = _save_lora(model, output_dir / "lora_weights.pt")

    # Save config
    config_out = {
        "rank": rank,
        "alpha": alpha,
        "epochs": epochs,
        "batch_size": batch_size,
        "lr": lr,
        "train_samples": len(train_data),
        "val_samples": len(val_data),
        "trainable_params": trainable,
        "total_params": total,
        "best_loss": round(best_loss, 4),
        "history": history,
        "base_model": str(base_model_path),
    }
    config_path = output_dir / "training_config.json"
    config_path.write_text(json.dumps(config_out, indent=2), encoding="utf-8")

    elapsed = time.time() - start_time
    logger.info("Training complete: %.0fs, best_loss=%.4f", elapsed, best_loss)

    return {
        "train_loss": history[-1]["train_loss"] if history else 0,
        "best_val_loss": best_loss,
        "epochs": epochs,
        "elapsed_seconds": round(elapsed, 1),
        "trainable_params": trainable,
        "lora_path": str(lora_path),
    }


def _evaluate(model: nn.Module, val_loader: torch.utils.data.DataLoader, device: torch.device) -> float:
    """Run evaluation on validation set."""
    model.eval()
    total_loss = 0.0
    num_batches = 0

    with torch.no_grad():
        for batch in val_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
            )
            total_loss += outputs.loss.item()
            num_batches += 1

    model.train()
    return total_loss / max(num_batches, 1)


def _save_lora(model: nn.Module, path: Path) -> Path:
    """Save LoRA weights to file."""
    lora_state = extract_lora_state_dict(model)
    torch.save(lora_state, path)
    size_mb = path.stat().st_size / (1024 * 1024)
    logger.info("Saved LoRA weights: %s (%.1f MB)", path, size_mb)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune BitNet b1.58 with STE-aware LoRA")
    parser.add_argument("--base-model", type=Path, required=True, help="Path to BitNet BF16 model")
    parser.add_argument("--data-dir", type=Path, default=Path("training_data/bitnet"))
    parser.add_argument("--output-dir", type=Path, default=Path("training_data/bitnet_finetuned"))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=8)
    parser.add_argument("--alpha", type=float, default=16.0)
    parser.add_argument("--warmup-steps", type=int, default=100)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--eval-every", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    results = train_bitnet_lora(
        base_model_path=args.base_model,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        rank=args.rank,
        alpha=args.alpha,
        warmup_steps=args.warmup_steps,
        max_length=args.max_length,
        eval_every=args.eval_every,
        seed=args.seed,
    )
    print(f"\nTraining complete: {results}")


if __name__ == "__main__":
    main()
