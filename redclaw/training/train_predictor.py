"""Training loop + evaluation for ToolCallPredictor.

Three improvements over v1:
  1. Sample weights: surgical successes (few tool calls) get 2x weight,
     failures get 0.3x weight
  2. Class weights: inverse frequency weighting to counter bash dominance
  3. Weighted loss: combines both sample + class weights

Usage:
    python -m redclaw.training.train_predictor --data-dir training_data/ --epochs 50
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset, random_split

from redclaw.training.binary_model import BinaryMLP
from redclaw.training.encode import (
    INPUT_SIZE,
    TOOL_VOCAB,
    encode_all_sequences,
)

logger = logging.getLogger(__name__)


def _compute_metrics(
    model: BinaryMLP,
    loader: DataLoader,
    device: torch.device,
) -> dict:
    """Compute accuracy, top-3 accuracy, and per-tool precision/recall."""
    model.eval()
    correct = 0
    top3_correct = 0
    total = 0
    num_classes = len(TOOL_VOCAB)

    class_correct = [0] * num_classes
    class_total = [0] * num_classes
    class_predicted = [0] * num_classes

    with torch.no_grad():
        for batch in loader:
            x, y = batch[0].to(device), batch[1].to(device)
            logits = model(x)
            _, pred = logits.max(1)
            _, top3 = logits.topk(3, dim=1)

            correct += (pred == y).sum().item()
            top3_correct += sum(y[i] in top3[i] for i in range(len(y)))
            total += len(y)

            for i in range(len(y)):
                label = y[i].item()
                class_total[label] += 1
                class_predicted[pred[i].item()] += 1
                if pred[i] == y[i]:
                    class_correct[label] += 1

    accuracy = correct / max(total, 1)
    top3_accuracy = top3_correct / max(total, 1)

    per_tool: dict[str, dict[str, float]] = {}
    for i, name in enumerate(TOOL_VOCAB):
        if class_total[i] > 0:
            per_tool[name] = {
                "precision": class_correct[i] / max(class_predicted[i], 1),
                "recall": class_correct[i] / class_total[i],
                "support": class_total[i],
            }

    return {
        "accuracy": accuracy,
        "top3_accuracy": top3_accuracy,
        "total": total,
        "per_tool": per_tool,
    }


def train(
    data_dir: Path,
    epochs: int = 50,
    batch_size: int = 32,
    lr: float = 1e-3,
    window: int = 5,
    eval_split: float = 0.2,
) -> dict:
    """Train ToolCallPredictor. Returns metrics dict."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Device: %s", device)

    # ── Load and encode data (now returns weighted triples) ──
    triples = encode_all_sequences(data_dir, window=window)
    if len(triples) < 10:
        logger.warning("Only %d training pairs — results may be unreliable", len(triples))

    xs = torch.stack([t[0] for t in triples])
    ys = torch.stack([t[1] for t in triples])
    sample_weights = torch.stack([t[2] for t in triples])

    # ── Class weights (inverse frequency) to counter bash dominance ──
    label_counts = Counter(ys.tolist())
    num_classes = len(TOOL_VOCAB)
    total_samples = len(ys)
    class_weights = torch.ones(num_classes)
    for c in range(num_classes):
        if c in label_counts:
            class_weights[c] = total_samples / (num_classes * label_counts[c])

    logger.info("Class weights: %s", {TOOL_VOCAB[i]: f"{class_weights[i]:.2f}" for i in range(num_classes) if i in label_counts})

    dataset = TensorDataset(xs, ys, sample_weights)

    # ── Train/eval split ──
    eval_size = max(1, int(len(dataset) * eval_split))
    train_size = len(dataset) - eval_size
    train_ds, eval_ds = random_split(dataset, [train_size, eval_size])

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    eval_loader = DataLoader(eval_ds, batch_size=batch_size)

    # ── Frequency baseline ──
    most_common_label, most_common_count = label_counts.most_common(1)[0]
    freq_baseline = most_common_count / len(ys)
    random_baseline = 1.0 / len(TOOL_VOCAB)

    # ── Model ──
    model = BinaryMLP(input_size=INPUT_SIZE, hidden_size=64, output_size=num_classes).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.CrossEntropyLoss(weight=class_weights.to(device), reduction="none")

    logger.info(
        "Training: %d triples (%d train, %d eval), %d epochs",
        len(triples), train_size, eval_size, epochs,
    )
    logger.info(
        "Baselines — random: %.1f%%, frequency (%s): %.1f%%",
        random_baseline * 100, TOOL_VOCAB[most_common_label], freq_baseline * 100,
    )

    # ── Training loop ──
    best_eval_acc = 0.0
    history: list[dict] = []

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        correct = 0
        total = 0

        for batch in train_loader:
            x, y, w = batch[0].to(device), batch[1].to(device), batch[2].to(device)
            logits = model(x)
            per_sample_loss = loss_fn(logits, y)
            # Combine class weights (inside loss_fn) + sample weights
            loss = (per_sample_loss * w).mean()

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item() * len(y)
            _, pred = logits.max(1)
            correct += (pred == y).sum().item()
            total += len(y)

        train_acc = correct / max(total, 1)
        avg_loss = epoch_loss / max(total, 1)

        # Eval
        eval_metrics = _compute_metrics(model, eval_loader, device)

        if eval_metrics["accuracy"] > best_eval_acc:
            best_eval_acc = eval_metrics["accuracy"]
            save_path = data_dir / "predictor.pt"
            torch.save(model.state_dict(), save_path)

        entry = {
            "epoch": epoch + 1,
            "loss": round(avg_loss, 4),
            "train_acc": round(train_acc, 4),
            "eval_acc": round(eval_metrics["accuracy"], 4),
            "eval_top3": round(eval_metrics["top3_accuracy"], 4),
        }
        history.append(entry)

        if (epoch + 1) % 10 == 0 or epoch == 0:
            logger.info(
                "Epoch %3d: loss=%.4f train_acc=%.3f eval_acc=%.3f eval_top3=%.3f",
                epoch + 1, avg_loss, train_acc,
                eval_metrics["accuracy"], eval_metrics["top3_accuracy"],
            )

    # ── Final evaluation ──
    final_metrics = _compute_metrics(model, eval_loader, device)
    final_metrics["best_eval_acc"] = best_eval_acc
    final_metrics["random_baseline"] = random_baseline
    final_metrics["freq_baseline"] = freq_baseline
    final_metrics["history"] = history
    final_metrics["train_samples"] = train_size
    final_metrics["eval_samples"] = eval_size

    # ── Summary ──
    print(f"\n{'='*50}")
    print(f"Training complete — {epochs} epochs, {len(triples)} samples")
    print(f"Best eval accuracy: {best_eval_acc:.1%}")
    print(f"Final eval accuracy: {final_metrics['accuracy']:.1%}")
    print(f"Final top-3 accuracy: {final_metrics['top3_accuracy']:.1%}")
    print(f"Baselines: random={random_baseline:.1%}, frequency={freq_baseline:.1%}")
    print(f"Model saved to: {data_dir / 'predictor.pt'}")
    print(f"\nPer-tool metrics:")
    for name, m in final_metrics.get("per_tool", {}).items():
        print(f"  {name:15s}: precision={m['precision']:.2f} recall={m['recall']:.2f} (n={m['support']})")
    print(f"{'='*50}")

    return final_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train ToolCallPredictor on exported sequences")
    parser.add_argument("--data-dir", type=Path, default=Path("training_data"))
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--eval-split", type=float, default=0.2)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    train(args.data_dir, args.epochs, args.batch_size, args.lr, eval_split=args.eval_split)


if __name__ == "__main__":
    main()
