"""Token saver — uses local BitNet model or BinaryMLP to skip predictable tool calls.

The TokenSaver intercepts tool calls before they reach the API and routes
predictable ones to the local model instead, saving API tokens.

Strategy:
- BinaryMLP: fast (~1ms), predicts next tool from sequence history
- LocalBitNet: slower (~50ms), uses full context, more accurate

Decision flow:
1. Build prompt from conversation context
2. Predict next tool via local model
3. If confidence > threshold → SKIP the API call (use local prediction)
4. If confidence < threshold → proceed with normal API call

Usage:
    python -m redclaw --token-saver --local-model path/to/model.gguf
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from redclaw.training.encode import TOOL_VOCAB, TOOL_TO_IDX, encode_sequence

logger = logging.getLogger(__name__)


@dataclass
class TokenSaverConfig:
    """Configuration for token saver."""
    model_path: Path | None = None  # GGUF model for BitNet
    bitnet_bin: Path | None = None  # bitnet.cpp binary
    mlp_weights: Path | None = None  # BinaryMLP weights
    skip_threshold: float = 0.85  # Confidence threshold for skipping API call
    enabled: bool = True
    use_bitnet_server: bool = False
    server_url: str = "http://127.0.0.1:8080"


@dataclass
class Prediction:
    """A tool call prediction result."""
    tool_name: str
    confidence: float
    source: str  # "mlp", "bitnet", or "unknown"
    skipped: bool = False  # Whether the API call was skipped


class TokenSaver:
    """Token-saving tool call predictor.

    Uses local models (BinaryMLP or BitNet) to predict tool calls
    and skip API calls when the prediction is confident enough.
    """

    def __init__(self, config: TokenSaverConfig) -> None:
        self.config = config
        self._mlp: Any = None
        self._bitnet: Any = None
        self._tool_history: list[str] = []
        self._stats = {
            "total_predictions": 0,
            "skipped_calls": 0,
            "correct_skips": 0,
            "wrong_skips": 0,
            "tokens_saved": 0,
        }

        # Initialize models
        self._init_mlp()
        self._init_bitnet()

    def _init_mlp(self) -> None:
        """Load BinaryMLP if weights available."""
        import torch
        weights_path = self.config.mlp_weights
        if weights_path is None:
            # Default path
            weights_path = Path("training_data/predictor.pt")
        if not weights_path.is_file():
            return

        try:
            from redclaw.training.binary_model import BinaryMLP
            model = BinaryMLP()
            state = torch.load(weights_path, map_location="cpu", weights_only=True)
            model.load_state_dict(state)
            model.eval()
            self._mlp = model
            logger.info("Loaded BinaryMLP from %s", weights_path)
        except Exception as e:
            logger.warning("Failed to load BinaryMLP: %s", e)

    def _init_bitnet(self) -> None:
        """Load LocalBitNet if model available."""
        if self.config.model_path is None or not self.config.model_path.is_file():
            return

        try:
            from redclaw.runtime.local_llm import LocalBitNet, BitNetConfig
            config = BitNetConfig(
                model_path=self.config.model_path,
                bitnet_bin=self.config.bitnet_bin,
                server_mode=self.config.use_bitnet_server,
                server_url=self.config.server_url,
            )
            self._bitnet = LocalBitNet(config)
            logger.info("Loaded LocalBitNet from %s", self.config.model_path)
        except Exception as e:
            logger.warning("Failed to load LocalBitNet: %s", e)

    def record_tool_call(self, tool_name: str) -> None:
        """Record a tool call in the history for sequence prediction."""
        self._tool_history.append(tool_name)

    def reset_history(self) -> None:
        """Reset tool call history for a new conversation turn."""
        self._tool_history.clear()

    async def predict_next_tool(
        self,
        context: str = "",
        instance_id: str = "",
    ) -> Prediction:
        """Predict the next tool call.

        Tries BitNet first (more accurate), falls back to BinaryMLP (faster).
        Returns a Prediction with confidence and skip recommendation.
        """
        if not self.config.enabled:
            return Prediction("unknown", 0.0, "unknown")

        self._stats["total_predictions"] += 1

        # Try BitNet first
        if self._bitnet:
            prompt = self._build_prompt(context)
            tool_name, confidence = await self._bitnet.predict_tool(prompt)
            if tool_name != "unknown":
                prediction = Prediction(tool_name, confidence, "bitnet")
                prediction.skipped = confidence >= self.config.skip_threshold
                if prediction.skipped:
                    self._stats["skipped_calls"] += 1
                    self._stats["tokens_saved"] += 500  # Estimated tokens per call
                return prediction

        # Fall back to BinaryMLP
        if self._mlp:
            import torch
            pairs = encode_sequence(
                self._tool_history or ["read_file"],
                instance_id or "unknown",
            )
            if pairs:
                x = pairs[-1][0].unsqueeze(0)  # Last prediction step
                with torch.no_grad():
                    logits = self._mlp(x)
                    probs = torch.softmax(logits, dim=-1)
                    confidence, pred_idx = probs.max(dim=-1)
                    tool_name = TOOL_VOCAB[pred_idx.item()]
                    prediction = Prediction(tool_name, confidence.item(), "mlp")
                    prediction.skipped = confidence.item() >= self.config.skip_threshold
                    if prediction.skipped:
                        self._stats["skipped_calls"] += 1
                        self._stats["tokens_saved"] += 500
                    return prediction

        return Prediction("unknown", 0.0, "unknown")

    def record_outcome(self, prediction: Prediction, actual_tool: str) -> None:
        """Record the outcome of a prediction for accuracy tracking."""
        if prediction.skipped:
            if prediction.tool_name == actual_tool:
                self._stats["correct_skips"] += 1
            else:
                self._stats["wrong_skips"] += 1

    def get_stats(self) -> dict[str, int | float]:
        """Get prediction statistics."""
        total = self._stats["total_predictions"]
        skipped = self._stats["skipped_calls"]
        correct = self._stats["correct_skips"]
        wrong = self._stats["wrong_skips"]

        skip_rate = (skipped / max(total, 1)) * 100
        accuracy = (correct / max(skipped, 1)) * 100

        return {
            **self._stats,
            "skip_rate": round(skip_rate, 1),
            "skip_accuracy": round(accuracy, 1),
        }

    def _build_prompt(self, context: str) -> str:
        """Build a prediction prompt for BitNet."""
        tools_str = ", ".join(self._tool_history[-5:]) if self._tool_history else "(none)"
        prompt = (
            f"You are RedClaw. Tools used so far: [{tools_str}]. "
            f"Predict the next tool to use. "
            f"Respond with only the tool name from: {', '.join(TOOL_VOCAB)}."
        )
        if context:
            prompt += f"\nContext: {context[:200]}"
        return prompt
