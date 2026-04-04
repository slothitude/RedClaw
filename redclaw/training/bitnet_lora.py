"""STE-aware LoRA for BitNet b1.58 models.

Architecture:
- Base weights: frozen ternary {-1, 0, +1}
- LoRA adapters: trainable fp16 low-rank matrices
- Forward: base_output + lora_output (residual)
- Backward: STE for base (identity), normal for LoRA

The key insight: BitNet's ternary weights can't be updated directly via
gradient descent. Instead, we freeze them and learn a low-rank correction
that gets merged post-training.

Trainable params: ~4M (rank-8 on all attention+FFN) vs 2.4B total = 0.17%
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

logger = logging.getLogger(__name__)


@dataclass
class BitNetLoRAConfig:
    """Configuration for STE-aware LoRA injection."""
    rank: int = 8
    alpha: float = 16.0
    target_modules: list[str] = field(default_factory=lambda: [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ])
    dropout: float = 0.05


class BitNetLoRALinear(nn.Module):
    """Linear layer with frozen ternary base + trainable LoRA adapter.

    Forward: output = base(x) + (alpha/rank) * x @ A @ B
    Backward: gradients flow through LoRA normally; base is frozen.
    """

    def __init__(
        self,
        base_weight: Tensor,
        rank: int = 8,
        alpha: float = 16.0,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        out_features, in_features = base_weight.shape

        # Frozen ternary base (no gradient)
        self.base = nn.Parameter(base_weight.clone(), requires_grad=False)

        # LoRA matrices (trainable)
        self.lora_A = nn.Parameter(torch.randn(in_features, rank) * 0.01)
        self.lora_B = nn.Parameter(torch.zeros(rank, out_features))

        self.alpha = alpha
        self.rank = rank
        self.scaling = alpha / rank
        self.dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()

    def forward(self, x: Tensor) -> Tensor:
        # Base: frozen ternary matmul (no gradient)
        base_out = F.linear(x, self.base)

        # LoRA: low-rank correction (trainable)
        x_drop = self.dropout(x)
        lora_out = (x_drop @ self.lora_A @ self.lora_B) * self.scaling

        return base_out + lora_out


def inject_lora(
    model: nn.Module,
    config: BitNetLoRAConfig,
) -> nn.Module:
    """Replace target linear layers with BitNetLoRALinear.

    For each target module:
    1. Extract frozen ternary weights
    2. Replace with BitNetLoRALinear (base frozen + LoRA trainable)
    3. Return modified model

    Returns the modified model (mutated in place).
    """
    replaced = 0
    total_params = 0
    trainable_params = 0

    for name, module in list(model.named_modules()):
        # Check if this module's name matches any target pattern
        module_name = name.split(".")[-1]
        if module_name not in config.target_modules:
            continue

        if not isinstance(module, nn.Linear):
            continue

        # Get parent module and attribute name
        parts = name.split(".")
        parent = model
        for part in parts[:-1]:
            parent = getattr(parent, part)
        attr_name = parts[-1]

        # Create LoRA replacement
        lora_layer = BitNetLoRALinear(
            base_weight=module.weight.data,
            rank=config.rank,
            alpha=config.alpha,
            dropout=config.dropout,
        )

        # Replace in parent
        setattr(parent, attr_name, lora_layer)
        replaced += 1

        # Count params
        total_params += module.weight.numel()
        trainable_params += lora_layer.lora_A.numel() + lora_layer.lora_B.numel()

    logger.info(
        "Injected LoRA: %d layers replaced, %d trainable params (%.2f%% of %d total)",
        replaced, trainable_params,
        (trainable_params / max(total_params, 1)) * 100,
        total_params,
    )

    return model


def extract_lora_state_dict(model: nn.Module) -> dict[str, Tensor]:
    """Extract only the LoRA weights from a model with injected LoRA layers.

    Returns a state dict with only LoRA A and B matrices — small enough
    to save as a ~16MB file for a rank-8 2B model.
    """
    lora_state = {}
    for name, param in model.named_parameters():
        if "lora_A" in name or "lora_B" in name:
            lora_state[name] = param.data.clone()
    return lora_state


def merge_lora_weights(
    model: nn.Module,
) -> dict[str, Tensor]:
    """Merge LoRA adapters into base weights for export.

    For each LoRA layer:
        W_merged = W_base + (alpha/rank) * A @ B

    Then re-quantize to ternary {-1, 0, +1}:
        W_ternary = sign(W_merged)

    Returns dict of {layer_name: ternary_weight}
    """
    merged_weights = {}

    for name, module in model.named_modules():
        if not isinstance(module, BitNetLoRALinear):
            continue

        # Merge: W = W_base + scaling * A @ B
        correction = module.lora_A.data @ module.lora_B.data  # (in, rank) @ (rank, out) = (in, out)
        merged = module.base.data + module.scaling * correction.T  # (out, in)

        # Re-quantize to ternary {-1, 0, +1}
        # BitNet b1.58 quantization: sign with threshold
        ternary = torch.sign(merged)
        # Apply magnitude threshold (values near 0 become 0)
        threshold = 0.05
        ternary[merged.abs() < threshold] = 0

        merged_weights[name] = ternary

    return merged_weights
