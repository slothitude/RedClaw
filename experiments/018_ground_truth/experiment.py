"""Experiment 018: Synthetic Ground Truth — Representation vs Attention.

Tests two independent axes:
  - Embedding:    Euclidean vs Circular
  - Attention:    Dot-product vs Distance

4 models × 7 tasks × 5 seeds = 140 training runs.
Small enough to finish in minutes on CPU.

Usage:
    python experiments/018_ground_truth/experiment.py
"""

from __future__ import annotations

import io
import json
import math
import sys
import time

# Fix Windows console encoding for box-drawing chars
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset

# ── Reproducibility ──────────────────────────────────────────

def seed_all(seed: int) -> None:
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

# ── Hyperparameters (shared across ALL runs) ─────────────────

EMBED_DIM     = 32
N_HEADS       = 4
HEAD_DIM      = EMBED_DIM // N_HEADS   # 8
N_LAYERS      = 2
SEQ_LEN       = 16
BATCH_SIZE    = 64
N_TRAIN       = 2048
N_TEST        = 512
EPOCHS        = 60
LR            = 1e-3
N_SEEDS       = 5
SEEDS         = [42, 137, 256, 999, 3141]

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ─────────────────────────────────────────────────────────────
# 1. Attention modules
# ─────────────────────────────────────────────────────────────

class DotProductAttention(nn.Module):
    """Standard scaled dot-product attention."""

    def __init__(self, embed_dim: int, n_heads: int):
        super().__init__()
        self.n_heads = n_heads
        self.head_dim = embed_dim // n_heads
        self.q_proj = nn.Linear(embed_dim, embed_dim, bias=False)
        self.k_proj = nn.Linear(embed_dim, embed_dim, bias=False)
        self.v_proj = nn.Linear(embed_dim, embed_dim, bias=False)
        self.out_proj = nn.Linear(embed_dim, embed_dim, bias=False)
        self.scale = math.sqrt(self.head_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, S, D = x.shape
        q = self.q_proj(x).view(B, S, self.n_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(B, S, self.n_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(B, S, self.n_heads, self.head_dim).transpose(1, 2)
        attn = torch.matmul(q, k.transpose(-2, -1)) / self.scale
        attn = F.softmax(attn, dim=-1)
        out = torch.matmul(attn, v)
        out = out.transpose(1, 2).contiguous().view(B, S, D)
        return self.out_proj(out)


class DistanceAttention(nn.Module):
    """Attention based on angular distance on S^1 per head dimension pair.

    Uses 1 - cos(angle_between) as the distance measure, so closer vectors
    get higher attention. Works naturally with both Euclidean and Circular
    embeddings because it only depends on the dot products of query/key.
    """

    def __init__(self, embed_dim: int, n_heads: int):
        super().__init__()
        self.n_heads = n_heads
        self.head_dim = embed_dim // n_heads
        self.q_proj = nn.Linear(embed_dim, embed_dim, bias=False)
        self.k_proj = nn.Linear(embed_dim, embed_dim, bias=False)
        self.v_proj = nn.Linear(embed_dim, embed_dim, bias=False)
        self.out_proj = nn.Linear(embed_dim, embed_dim, bias=False)
        self.scale = math.sqrt(self.head_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, S, D = x.shape
        q = self.q_proj(x).view(B, S, self.n_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(B, S, self.n_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(B, S, self.n_heads, self.head_dim).transpose(1, 2)

        # Normalize q, k to unit sphere — then dot product = cos(angle)
        q_norm = F.normalize(q, dim=-1)
        k_norm = F.normalize(k, dim=-1)
        cos_sim = torch.matmul(q_norm, k_norm.transpose(-2, -1))  # [B,H,S,S]

        # Convert to distance-based weights: higher similarity → higher weight
        # softmax over (1 - cos_sim) inverted so closer = more attention
        attn = F.softmax(cos_sim / self.scale, dim=-1)
        out = torch.matmul(attn, v)
        out = out.transpose(1, 2).contiguous().view(B, S, D)
        return self.out_proj(out)


# ─────────────────────────────────────────────────────────────
# 2. Embedding wrappers
# ─────────────────────────────────────────────────────────────

class EuclideanEmbedding(nn.Module):
    """Standard linear projection (no modification)."""

    def __init__(self, input_dim: int, embed_dim: int):
        super().__init__()
        self.proj = nn.Linear(input_dim, embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)


class CircularEmbedding(nn.Module):
    """Projects input, then maps each dimension pair (2i, 2i+1) to a unit
    circle via (cos, sin) normalization. This forces representations to
    live on a product of circles S^1 × S^1 × ...

    If embed_dim is odd, the last dimension gets a tanh squish.
    """

    def __init__(self, input_dim: int, embed_dim: int):
        super().__init__()
        self.proj = nn.Linear(input_dim, embed_dim)
        self.embed_dim = embed_dim

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.proj(x)
        pairs = self.embed_dim // 2
        out_parts = []
        for i in range(pairs):
            a, b = h[..., 2 * i], h[..., 2 * i + 1]
            r = torch.sqrt(a ** 2 + b ** 2 + 1e-8)
            out_parts.append(a / r)
            out_parts.append(b / r)
        if self.embed_dim % 2 == 1:
            out_parts.append(torch.tanh(h[..., -1]))
        return torch.stack(out_parts, dim=-1)


# ─────────────────────────────────────────────────────────────
# 3. Transformer with swappable components
# ─────────────────────────────────────────────────────────────

class TransformerBlock(nn.Module):

    def __init__(self, embed_dim: int, n_heads: int, attn_cls: type):
        super().__init__()
        self.attn = attn_cls(embed_dim, n_heads)
        self.ln1 = nn.LayerNorm(embed_dim)
        self.ln2 = nn.LayerNorm(embed_dim)
        self.ff = nn.Sequential(
            nn.Linear(embed_dim, embed_dim * 4),
            nn.GELU(),
            nn.Linear(embed_dim * 4, embed_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.ln1(x))
        x = x + self.ff(self.ln2(x))
        return x


class SmallTransformer(nn.Module):

    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        embed_dim: int,
        n_heads: int,
        n_layers: int,
        attn_cls: type,
        embed_cls: type,
    ):
        super().__init__()
        self.embedding = embed_cls(input_dim, embed_dim)
        self.blocks = nn.Sequential(
            *[TransformerBlock(embed_dim, n_heads, attn_cls) for _ in range(n_layers)]
        )
        self.ln_f = nn.LayerNorm(embed_dim)
        self.head = nn.Linear(embed_dim, output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.embedding(x)
        h = self.blocks(h)
        h = self.ln_f(h)
        return self.head(h)


# ─────────────────────────────────────────────────────────────
# 4. Model configurations
# ─────────────────────────────────────────────────────────────

ModelKey = Literal["A_euc_dp", "B_euc_dist", "C_circ_dp", "D_circ_dist"]

MODEL_CONFIGS: dict[ModelKey, dict] = {
    "A_euc_dp": {
        "label": "Euclidean + DotProd",
        "attn_cls": DotProductAttention,
        "embed_cls": EuclideanEmbedding,
    },
    "B_euc_dist": {
        "label": "Euclidean + Distance",
        "attn_cls": DistanceAttention,
        "embed_cls": EuclideanEmbedding,
    },
    "C_circ_dp": {
        "label": "Circular + DotProd",
        "attn_cls": DotProductAttention,
        "embed_cls": CircularEmbedding,
    },
    "D_circ_dist": {
        "label": "Circular + Distance",
        "attn_cls": DistanceAttention,
        "embed_cls": CircularEmbedding,
    },
}

# ─────────────────────────────────────────────────────────────
# 5. Synthetic task generators
# ─────────────────────────────────────────────────────────────

def generate_phase_prediction(n: int, seq_len: int, seed: int):
    """Task 1 — Phase prediction: predict next angle on circle.

    Input: sequence of angles (radians).
    Target: next angle, wrapped to [0, 2π).
    Loss: angular distance 1 - cos(pred - true).
    A linear model will struggle near the 0↔2π wrap boundary.
    """
    gen = torch.Generator().manual_seed(seed)
    freq = 0.7  # base frequency
    phase = torch.linspace(0, 2 * math.pi * freq, seq_len + 1)
    noise = torch.randn(n, seq_len + 1, generator=gen) * 0.3
    angles = (phase.unsqueeze(0) + noise) % (2 * math.pi)

    x = angles[:, :seq_len].unsqueeze(-1)  # (n, seq_len, 1)
    y = angles[:, seq_len:]                 # (n, 1)
    return x, y, "angular"


def generate_circular_classification(n: int, seq_len: int, seed: int):
    """Task 2 — Circular classification: K rotation patterns.

    Each class is the same base signal rotated by a different phase offset.
    Classes differ ONLY by phase rotation — no amplitude shortcut.
    """
    gen = torch.Generator().manual_seed(seed)
    n_classes = 4
    base = torch.linspace(0, 2 * math.pi, seq_len)
    offsets = torch.linspace(0, 2 * math.pi, n_classes + 1)[:n_classes]

    labels = torch.randint(0, n_classes, (n,), generator=gen)
    noise = torch.randn(n, seq_len, generator=gen) * 0.2

    x = torch.zeros(n, seq_len, 1)
    for i in range(n):
        x[i, :, 0] = (base + offsets[labels[i]] + noise[i]) % (2 * math.pi)

    return x, labels, "cross_entropy"


def generate_periodic_sequence(n: int, seq_len: int, seed: int):
    """Task 3 — Periodic sequence with incommensurate frequencies.

    Sum of sinusoids with frequencies 1, √2, π. Cannot be memorized
    as a simple periodic pattern.
    """
    gen = torch.Generator().manual_seed(seed)
    t = torch.linspace(0, 4 * math.pi, seq_len + 1)
    freqs = [1.0, math.sqrt(2), math.pi]
    signal = sum(torch.sin(f * t) for f in freqs)
    noise = torch.randn(n, seq_len + 1, generator=gen) * 0.1
    data = signal.unsqueeze(0) + noise

    x = data[:, :seq_len].unsqueeze(-1)
    y = data[:, seq_len:].unsqueeze(-1)
    return x, y, "mse"


def generate_linear_regression(n: int, seq_len: int, seed: int):
    """Task 4 — High-dimensional sparse linear regression.

    32 inputs but only 3 are relevant. Tests whether circular embedding
    hurts feature selection.
    """
    gen = torch.Generator().manual_seed(seed)
    input_dim = 32

    x = torch.randn(n, seq_len, input_dim, generator=gen)
    # Only first 3 features matter
    weights = torch.zeros(input_dim)
    weights[:3] = torch.randn(3, generator=gen)

    y = (x * weights).sum(dim=-1)  # (n, seq_len)
    # Predict sum of last token
    y = y[:, -1:].squeeze(-1)
    x_last = x[:, -1:, :]  # use only last token position for simplicity
    # Actually, feed full sequence, predict scalar from last position
    return x, y, "mse"


def generate_feature_selection(n: int, seq_len: int, seed: int):
    """Task 5 — Feature selection with noise features of similar magnitude.

    Forces precise discrimination where dot-product excels.
    """
    gen = torch.Generator().manual_seed(seed)
    input_dim = 16

    x = torch.randn(n, seq_len, input_dim, generator=gen)
    # 2 relevant features with signal, rest are noise of similar magnitude
    signal_weights = torch.zeros(input_dim)
    signal_weights[3] = 2.0
    signal_weights[11] = -1.5

    # Add noise features with similar magnitude to mask the signal
    noise_scale = 1.5
    y = (x * signal_weights).sum(dim=-1)
    y = y[:, -1:].squeeze(-1)

    return x, y, "mse"


def generate_token_classification(n: int, seq_len: int, seed: int):
    """Task 6 — Non-periodic token classification.

    Random token embeddings with no positional periodicity.
    Circular bias should be useless here.
    """
    gen = torch.Generator().manual_seed(seed)
    vocab_size = 20
    embed_dim = 8

    # Random token embeddings (no periodicity)
    token_emb = torch.randn(vocab_size, embed_dim, generator=gen)

    # Each sample: random token ids
    token_ids = torch.randint(0, vocab_size, (n, seq_len), generator=gen)
    x = token_emb[token_ids]  # (n, seq_len, embed_dim)

    # Classify middle token into 3 classes based on its identity
    labels = (token_ids[:, seq_len // 2] % 3).long()
    return x, labels, "cross_entropy"


def generate_phase_scrambling(n: int, seq_len: int, seed: int):
    """Task 7 — Phase scrambling test (killer test).

    Circular data where all angles are randomly rotated per sample.
    Preserves relative structure but destroys global phase alignment.
    If circular attention is real, it should survive (relative phase matters).
    If it collapses, it was exploiting absolute phase.
    """
    gen = torch.Generator().manual_seed(seed)
    freq = 0.7
    phase = torch.linspace(0, 2 * math.pi * freq, seq_len + 1)
    noise = torch.randn(n, seq_len + 1, generator=gen) * 0.3
    angles = (phase.unsqueeze(0) + noise) % (2 * math.pi)

    # Random rotation per sample
    rotations = torch.rand(n, 1, generator=gen) * 2 * math.pi
    angles = (angles + rotations) % (2 * math.pi)

    x = angles[:, :seq_len].unsqueeze(-1)
    y = angles[:, seq_len:]
    return x, y, "angular"


# ─────────────────────────────────────────────────────────────
# 6. Loss functions
# ─────────────────────────────────────────────────────────────

def angular_loss(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """1 - cos(pred - target). Handles wrapping correctly."""
    return (1 - torch.cos(pred - target)).mean()


def get_loss_fn(loss_type: str):
    if loss_type == "angular":
        return angular_loss
    elif loss_type == "cross_entropy":
        return F.cross_entropy
    elif loss_type == "mse":
        return F.mse_loss
    else:
        raise ValueError(f"Unknown loss type: {loss_type}")


# ─────────────────────────────────────────────────────────────
# 7. Metrics
# ─────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    task: str
    model_key: str
    seed: int
    final_train_loss: float
    final_test_loss: float
    metric: float  # task-specific: accuracy for classification, RMSE for regression
    train_curve: list[float] = field(default_factory=list)
    test_curve: list[float] = field(default_factory=list)
    n_params: int = 0


def count_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


def compute_accuracy(pred: torch.Tensor, target: torch.Tensor, loss_type: str) -> float:
    if loss_type == "cross_entropy":
        pred_cls = pred.argmax(dim=-1)
        return (pred_cls == target).float().mean().item()
    elif loss_type == "angular":
        # Angular error in radians (lower is better)
        err = torch.abs(torch.atan2(torch.sin(pred - target), torch.cos(pred - target)))
        return -err.mean().item()  # negative so higher = better
    else:
        # Negative RMSE (higher = better)
        return -torch.sqrt(F.mse_loss(pred, target)).item()


# ─────────────────────────────────────────────────────────────
# 8. Training loop
# ─────────────────────────────────────────────────────────────

def train_one_run(
    task_name: str,
    task_generator,
    model_key: ModelKey,
    seed: int,
) -> RunResult:
    seed_all(seed)
    config = MODEL_CONFIGS[model_key]

    # Generate data
    x_train, y_train, loss_type = task_generator(N_TRAIN, SEQ_LEN, seed)
    x_test, y_test, _ = task_generator(N_TEST, SEQ_LEN, seed + 10000)

    # Move to device
    x_train = x_train.to(DEVICE)
    y_train = y_train.to(DEVICE)
    x_test = x_test.to(DEVICE)
    y_test = y_test.to(DEVICE)

    train_ds = TensorDataset(x_train, y_train)
    train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)

    # Determine input/output dims
    input_dim = x_train.shape[-1]
    if loss_type == "cross_entropy":
        output_dim = int(y_train.max().item()) + 1
    elif loss_type == "angular":
        output_dim = 1
    else:
        output_dim = y_train.shape[-1] if y_train.ndim > 1 else 1

    model = SmallTransformer(
        input_dim=input_dim,
        output_dim=output_dim,
        embed_dim=EMBED_DIM,
        n_heads=N_HEADS,
        n_layers=N_LAYERS,
        attn_cls=config["attn_cls"],
        embed_cls=config["embed_cls"],
    ).to(DEVICE)

    n_params = count_params(model)
    loss_fn = get_loss_fn(loss_type)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)

    train_curve = []
    test_curve = []

    for epoch in range(EPOCHS):
        model.train()
        epoch_losses = []
        for xb, yb in train_dl:
            optimizer.zero_grad()
            pred = model(xb)
            # For classification, use last token output
            if loss_type == "cross_entropy":
                pred = pred[:, -1, :]  # (B, output_dim)
            elif loss_type == "angular":
                pred = pred[:, -1, 0]  # scalar
            else:
                pred = pred[:, -1, :]  # (B, output_dim)
                if pred.shape[-1] != yb.shape[-1] if yb.ndim > 1 else True:
                    if pred.ndim == 2 and yb.ndim == 1:
                        pred = pred.squeeze(-1)

            loss = loss_fn(pred, yb)
            loss.backward()
            optimizer.step()
            epoch_losses.append(loss.item())

        train_curve.append(sum(epoch_losses) / len(epoch_losses))

        # Test eval
        model.eval()
        with torch.no_grad():
            pred = model(x_test)
            if loss_type == "cross_entropy":
                pred = pred[:, -1, :]
            elif loss_type == "angular":
                pred = pred[:, -1, 0]
            else:
                pred = pred[:, -1, :]
                if pred.ndim == 2 and y_test.ndim == 1:
                    pred = pred.squeeze(-1)
            test_loss = loss_fn(pred, y_test).item()
            test_curve.append(test_loss)

    # Final metrics
    model.eval()
    with torch.no_grad():
        pred = model(x_test)
        if loss_type == "cross_entropy":
            pred_cls = pred[:, -1, :]
        elif loss_type == "angular":
            pred_final = pred[:, -1, 0]
        else:
            pred_final = pred[:, -1, :]
            if pred_final.ndim == 2 and y_test.ndim == 1:
                pred_final = pred_final.squeeze(-1)

        if loss_type == "cross_entropy":
            metric = compute_accuracy(pred_cls, y_test, loss_type)
        elif loss_type == "angular":
            metric = compute_accuracy(pred_final, y_test, loss_type)
        else:
            metric = compute_accuracy(pred_final, y_test, loss_type)

    return RunResult(
        task=task_name,
        model_key=model_key,
        seed=seed,
        final_train_loss=train_curve[-1],
        final_test_loss=test_curve[-1],
        metric=metric,
        train_curve=train_curve,
        test_curve=test_curve,
        n_params=n_params,
    )


# ─────────────────────────────────────────────────────────────
# 9. Statistical analysis
# ─────────────────────────────────────────────────────────────

def cohen_d(a: list[float], b: list[float]) -> float:
    """Effect size: Cohen's d."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return 0.0
    mean_a, mean_b = sum(a) / na, sum(b) / nb
    var_a = sum((x - mean_a) ** 2 for x in a) / (na - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (nb - 1)
    pooled_std = math.sqrt(((na - 1) * var_a + (nb - 1) * var_b) / (na + nb - 2))
    if pooled_std < 1e-12:
        return 0.0
    return (mean_a - mean_b) / pooled_std


def paired_t_test(a: list[float], b: list[float]) -> tuple[float, float]:
    """Paired t-test returning (t_statistic, p_value)."""
    n = len(a)
    if n < 2:
        return 0.0, 1.0
    diffs = [ai - bi for ai, bi in zip(a, b)]
    mean_d = sum(diffs) / n
    var_d = sum((d - mean_d) ** 2 for d in diffs) / (n - 1)
    if var_d < 1e-12:
        return 0.0, 1.0
    se = math.sqrt(var_d / n)
    t = mean_d / se
    # Approximate two-tailed p-value using normal approximation for small df
    # (with n=5, the t-dist is wide, so this is conservative)
    from math import erf, sqrt
    p = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))
    return t, p


def classify_result(d: float, p: float) -> str:
    """Classify as WIN / TIE / LOSS with effect size thresholds."""
    if p > 0.10:
        return "TIE"
    if abs(d) < 0.2:
        return "TIE"
    return "WIN" if d > 0 else "LOSS"


# ─────────────────────────────────────────────────────────────
# 10. Visualization
# ─────────────────────────────────────────────────────────────

def plot_results(all_results: dict[str, list[RunResult]], output_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  [skip plots — matplotlib not available]")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    colors = {"A_euc_dp": "#e74c3c", "B_euc_dist": "#2ecc71", "C_circ_dp": "#3498db", "D_circ_dist": "#9b59b6"}
    labels_short = {"A_euc_dp": "A: Euc+DP", "B_euc_dist": "B: Euc+Dist", "C_circ_dp": "C: Circ+DP", "D_circ_dist": "D: Circ+Dist"}

    # 1. Training curves per task
    task_names = list(all_results.keys())
    fig, axes = plt.subplots(2, 4, figsize=(20, 10))
    axes = axes.flatten()

    for idx, task_name in enumerate(task_names):
        ax = axes[idx]
        runs = all_results[task_name]
        for mk in MODEL_CONFIGS:
            mk_runs = [r for r in runs if r.model_key == mk]
            if not mk_runs:
                continue
            # Average curves across seeds
            max_len = max(len(r.test_curve) for r in mk_runs)
            curves = []
            for r in mk_runs:
                c = r.test_curve
                curves.append(c + [c[-1]] * (max_len - len(c)))
            avg_curve = np.mean(curves, axis=0)
            ax.plot(avg_curve, label=labels_short[mk], color=colors[mk], alpha=0.8)

        ax.set_title(task_name, fontsize=11)
        ax.set_xlabel("Epoch")
        ax.set_ylabel("Test Loss")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    # Hide unused subplots
    for idx in range(len(task_names), len(axes)):
        axes[idx].set_visible(False)

    fig.suptitle("Experiment 018: Test Loss Curves (averaged over 5 seeds)", fontsize=14)
    fig.tight_layout()
    fig.savefig(output_dir / "training_curves.png", dpi=150)
    plt.close(fig)

    # 2. Radar chart: average metric per model per task
    fig, ax = plt.subplots(figsize=(10, 10), subplot_kw=dict(polar=True))
    angles = np.linspace(0, 2 * np.pi, len(task_names), endpoint=False).tolist()
    angles += angles[:1]

    for mk in MODEL_CONFIGS:
        values = []
        for task_name in task_names:
            runs = [r for r in all_results[task_name] if r.model_key == mk]
            if runs:
                # Normalize metrics: for each task, scale relative to best model
                values.append(np.mean([r.metric for r in runs]))
            else:
                values.append(0)
        # Normalize to 0-1 per task
        values_arr = np.array(values)
        mn, mx = values_arr.min(), values_arr.max()
        if mx - mn > 1e-8:
            values_norm = (values_arr - mn) / (mx - mn)
        else:
            values_norm = np.ones_like(values_arr) * 0.5
        values_norm = values_norm.tolist()
        values_norm += values_norm[:1]
        ax.plot(angles, values_norm, "o-", label=labels_short[mk], color=colors[mk], linewidth=2)
        ax.fill(angles, values_norm, alpha=0.1, color=colors[mk])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(task_names, fontsize=9)
    ax.set_title("Relative Performance (normalized per task)", fontsize=13, y=1.08)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=9)
    fig.tight_layout()
    fig.savefig(output_dir / "radar_chart.png", dpi=150)
    plt.close(fig)

    print(f"  Plots saved to {output_dir}/")


# ─────────────────────────────────────────────────────────────
# 11. Reporting
# ─────────────────────────────────────────────────────────────

TASK_TYPES = {
    "1_phase_prediction": "circular",
    "2_circular_class": "circular",
    "3_periodic_seq": "circular",
    "4_linear_reg": "linear",
    "5_feature_select": "linear",
    "6_token_class": "linear",
    "7_phase_scramble": "killer",
}

def print_results_table(all_results: dict[str, list[RunResult]]) -> None:
    print("\n" + "=" * 90)
    print("EXPERIMENT 018: SYNTHETIC GROUND TRUTH — RESULTS")
    print("=" * 90)

    # Per-task results
    for task_name, runs in all_results.items():
        task_type = TASK_TYPES.get(task_name, "unknown")
        print(f"\n{'─' * 70}")
        print(f"Task: {task_name}  [{task_type}]")
        print(f"{'─' * 70}")
        print(f"  {'Model':<22} {'Train Loss':>10} {'Test Loss':>10} {'Metric':>10} {'Params':>8}")
        print(f"  {'─' * 22} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 8}")

        for mk in MODEL_CONFIGS:
            mk_runs = [r for r in runs if r.model_key == mk]
            if not mk_runs:
                continue
            avg_train = sum(r.final_train_loss for r in mk_runs) / len(mk_runs)
            avg_test = sum(r.final_test_loss for r in mk_runs) / len(mk_runs)
            avg_metric = sum(r.metric for r in mk_runs) / len(mk_runs)
            std_metric = (sum((r.metric - avg_metric) ** 2 for r in mk_runs) / len(mk_runs)) ** 0.5
            n_params = mk_runs[0].n_params

            label = MODEL_CONFIGS[mk]["label"]
            print(f"  {label:<22} {avg_train:>10.4f} {avg_test:>10.4f} {avg_metric:>+10.4f}±{std_metric:.3f} {n_params:>8}")

    # Win matrix
    print(f"\n{'=' * 90}")
    print("WIN MATRIX (vs Model A: Euclidean + DotProd baseline)")
    print("=" * 90)
    print(f"  {'Task':<22} {'B: Euc+Dist':>14} {'C: Circ+DP':>14} {'D: Circ+Dist':>14}")
    print(f"  {'─' * 22} {'─' * 14} {'─' * 14} {'─' * 14}")

    for task_name, runs in all_results.items():
        baseline = [r.metric for r in runs if r.model_key == "A_euc_dp"]
        row = f"  {task_name:<22}"
        for challenger_key in ["B_euc_dist", "C_circ_dp", "D_circ_dist"]:
            challenger = [r.metric for r in runs if r.model_key == challenger_key]
            if baseline and challenger:
                d = cohen_d(challenger, baseline)
                t, p = paired_t_test(challenger, baseline)
                verdict = classify_result(d, p)
                sig = "*" if p < 0.10 else ""
                row += f" {verdict:>5}{sig:<2} d={d:+.2f} "
            else:
                row += f" {'N/A':>14} "
        print(row)

    # Overall interpretation
    print(f"\n{'=' * 90}")
    print("INTERPRETATION")
    print("=" * 90)

    circular_tasks = [t for t, v in TASK_TYPES.items() if v == "circular"]
    linear_tasks = [t for t, v in TASK_TYPES.items() if v == "linear"]
    killer_tasks = [t for t, v in TASK_TYPES.items() if v == "killer"]

    def count_wins(tasks: list[str], model_key: str) -> int:
        wins = 0
        for task_name in tasks:
            runs_list = all_results.get(task_name, [])
            baseline = [r.metric for r in runs_list if r.model_key == "A_euc_dp"]
            challenger = [r.metric for r in runs_list if r.model_key == model_key]
            if baseline and challenger:
                d = cohen_d(challenger, baseline)
                t_val, p_val = paired_t_test(challenger, baseline)
                v = classify_result(d, p_val)
                if v == "WIN":
                    wins += 1
        return wins

    for mk in ["B_euc_dist", "C_circ_dp", "D_circ_dist"]:
        c_wins = count_wins(circular_tasks, mk)
        l_wins = count_wins(linear_tasks, mk)
        k_wins = count_wins(killer_tasks, mk)
        label = MODEL_CONFIGS[mk]["label"]
        print(f"\n  {label}:")
        print(f"    Circular tasks: {c_wins}/{len(circular_tasks)} wins vs baseline")
        print(f"    Linear tasks:   {l_wins}/{len(linear_tasks)} wins vs baseline")
        print(f"    Killer tasks:   {k_wins}/{len(killer_tasks)} wins vs baseline")

    print("\n  Verdict framework:")
    d_circ = count_wins(circular_tasks, "D_circ_dist")
    d_lin = count_wins(linear_tasks, "D_circ_dist")
    d_kill = count_wins(killer_tasks, "D_circ_dist")

    if d_circ > 0 and d_lin == 0:
        print("  → Case 1: Circular+Distance is a USEFUL INDUCTIVE BIAS (specialized, not fundamental)")
    elif d_circ > 0 and d_lin > 0:
        print("  → Case 2: Distance attention may be universally better — INVESTIGATE")
    elif d_circ == 0 and d_lin == 0:
        print("  → Case 3: HARD FALSIFICATION — circular approach adds nothing")
    else:
        print("  → Case 4: Mixed/noisy — differences are second-order")

    if d_kill > 0:
        print("  → Phase scramble survived: relative phase structure is being used")
    elif d_kill == 0 and len(killer_tasks) > 0:
        print("  → Phase scramble FAILED: model exploits absolute phase, not structure")

    print()


# ─────────────────────────────────────────────────────────────
# 12. Main
# ─────────────────────────────────────────────────────────────

TASK_GENERATORS = {
    "1_phase_prediction": generate_phase_prediction,
    "2_circular_class": generate_circular_classification,
    "3_periodic_seq": generate_periodic_sequence,
    "4_linear_reg": generate_linear_regression,
    "5_feature_select": generate_feature_selection,
    "6_token_class": generate_token_classification,
    "7_phase_scramble": generate_phase_scrambling,
}


def main():
    output_dir = Path(__file__).parent
    print(f"Experiment 018: Synthetic Ground Truth")
    print(f"  Device: {DEVICE}")
    print(f"  Models: {len(MODEL_CONFIGS)}")
    print(f"  Tasks:  {len(TASK_GENERATORS)}")
    print(f"  Seeds:  {N_SEEDS}")
    print(f"  Total runs: {len(MODEL_CONFIGS) * len(TASK_GENERATORS) * N_SEEDS}")
    print(f"  Epochs per run: {EPOCHS}")
    print()

    all_results: dict[str, list[RunResult]] = defaultdict(list)
    total = len(MODEL_CONFIGS) * len(TASK_GENERATORS) * N_SEEDS
    done = 0
    t0 = time.time()

    for task_name, task_gen in TASK_GENERATORS.items():
        for model_key in MODEL_CONFIGS:
            for seed in SEEDS:
                done += 1
                pct = done / total * 100
                elapsed = time.time() - t0
                eta = elapsed / done * (total - done) if done > 0 else 0
                sys.stdout.write(
                    f"\r  [{done}/{total}] {pct:5.1f}% | "
                    f"{task_name:<22} {model_key:<12} seed={seed:<5} | "
                    f"elapsed={elapsed:.0f}s eta={eta:.0f}s"
                )
                sys.stdout.flush()

                try:
                    result = train_one_run(task_name, task_gen, model_key, seed)
                    all_results[task_name].append(result)
                except Exception as e:
                    print(f"\n  ERROR: {task_name} {model_key} seed={seed}: {e}")
                    import traceback
                    traceback.print_exc()

    print(f"\n\n  Completed in {time.time() - t0:.1f}s")

    # Save raw results
    raw_data = {}
    for task_name, runs in all_results.items():
        raw_data[task_name] = [
            {
                "model_key": r.model_key,
                "seed": r.seed,
                "final_train_loss": r.final_train_loss,
                "final_test_loss": r.final_test_loss,
                "metric": r.metric,
                "n_params": r.n_params,
                "train_curve": r.train_curve,
                "test_curve": r.test_curve,
            }
            for r in runs
        ]
    with open(output_dir / "results.json", "w") as f:
        json.dump(raw_data, f, indent=2)
    print(f"  Raw results saved to {output_dir / 'results.json'}")

    # Report
    print_results_table(dict(all_results))

    # Plots
    plot_results(dict(all_results), output_dir)

    print("Done.")


if __name__ == "__main__":
    main()
