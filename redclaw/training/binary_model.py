"""Binary MLP with proper Straight-Through Estimator (STE) for tool call prediction.

Key fix from broken proposal: uses real STE where gradients flow through
real weights during backward pass, while forward uses binary {-1, +1} weights.

STE identity: output = x + (sign(x).detach() - x.detach())
  - Forward: sign(x) (binary)
  - Backward: gradient passes through x (real-valued)
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor


class BinaryLinear(nn.Module):
    """Linear layer with binary weights {-1, +1} and proper STE."""

    def __init__(self, in_features: int, out_features: int) -> None:
        super().__init__()
        # Initialize small so sign() is meaningful from the start
        self.weight = nn.Parameter(torch.randn(out_features, in_features) * 0.01)
        self.bias = nn.Parameter(torch.zeros(out_features))

    def forward(self, x: Tensor) -> Tensor:
        # STE: forward uses sign, backward passes gradient through real weights
        bin_weight = self.weight + (torch.sign(self.weight).detach() - self.weight.detach())
        return F.linear(x, bin_weight, self.bias)


class BinaryMLP(nn.Module):
    """2-layer binary MLP for tool call prediction.

    Args:
        input_size: Feature vector size (default 47 from encode.py)
        hidden_size: Hidden layer size
        output_size: Number of tool classes (default 8 from TOOL_VOCAB)
    """

    def __init__(
        self,
        input_size: int = 47,
        hidden_size: int = 64,
        output_size: int = 8,
    ) -> None:
        super().__init__()
        self.fc1 = BinaryLinear(input_size, hidden_size)
        self.fc2 = BinaryLinear(hidden_size, output_size)

    def forward(self, x: Tensor) -> Tensor:
        x = self.fc1(x)
        # STE activation: binary sign with gradient passthrough
        x = x.sign() + (x.detach() - x.sign().detach())
        return self.fc2(x)
