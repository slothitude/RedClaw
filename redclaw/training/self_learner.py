"""Self-learning system — retrain predictors from Crypt data.

Manages automatic retraining of both BinaryMLP and BitNet LoRA
as new entombed records accumulate in the Crypt.

GPU scheduling:
- GPU available: retrains BitNet LoRA (higher quality, needs GPU)
- CPU only: retrains BinaryMLP (lower quality, runs on CPU)

Trigger: 50+ new entombments since last retrain.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RetrainResult:
    """Result of a retrain operation."""
    model_type: str  # "mlp" or "lora"
    success: bool
    accuracy: float = 0.0
    samples_used: int = 0
    elapsed_seconds: float = 0.0
    message: str = ""


@dataclass
class LoRAConfig:
    """Configuration for BitNet LoRA retraining."""
    base_model_path: Path = Path("models/BitNet-2B")
    rank: int = 8
    alpha: float = 16.0
    epochs: int = 3
    batch_size: int = 4
    lr: float = 2e-4


class PredictorSelfLearner:
    """Automatic predictor retraining from Crypt data."""

    def __init__(
        self,
        data_dir: Path | None = None,
        retrain_threshold: int = 50,
        lora_config: LoRAConfig | None = None,
    ) -> None:
        self.data_dir = data_dir or Path("training_data")
        self.retrain_threshold = retrain_threshold
        self.lora_config = lora_config
        self._last_retrain_count = 0
        self._last_retrain_time = 0.0

        # Load state
        self._state_path = self.data_dir / ".self_learner_state.json"
        self._load_state()

    def _load_state(self) -> None:
        """Load retrain state from disk."""
        if self._state_path.is_file():
            try:
                state = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._last_retrain_count = state.get("last_retrain_count", 0)
                self._last_retrain_time = state.get("last_retrain_time", 0.0)
            except Exception:
                pass

    def _save_state(self) -> None:
        """Save retrain state to disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "last_retrain_count": self._last_retrain_count,
            "last_retrain_time": self._last_retrain_time,
        }
        self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def should_retrain(self, total_entombed: int) -> bool:
        """Check if retraining should be triggered."""
        new_records = total_entombed - self._last_retrain_count
        if new_records < self.retrain_threshold:
            return False
        # Don't retrain more than once per hour
        if time.time() - self._last_retrain_time < 3600:
            return False
        return True

    def retrain(self, crypt: Any) -> RetrainResult:
        """Retrain the appropriate model from Crypt data.

        Uses BitNet LoRA if config available + GPU, else BinaryMLP.
        """
        start_time = time.time()

        # Count total entombed records
        entombed_dir = Path(crypt.crypt_dir) / "entombed"
        total_entombed = len(list(entombed_dir.glob("sub-*.json"))) if entombed_dir.is_dir() else 0

        # Export fresh data
        try:
            from redclaw.training.export_dataset import export
            counts = export(
                crypt_dir=Path(crypt.crypt_dir),
                output_dir=self.data_dir,
            )
        except Exception as e:
            return RetrainResult("unknown", False, message=f"Export failed: {e}")

        # Generate synthetic data to augment
        try:
            from redclaw.training.generate_dataset import generate_dataset
            generate_dataset(self.data_dir, variations=10)
        except Exception as e:
            logger.warning("Synthetic data generation failed: %s", e)

        # Choose retraining strategy
        if self.lora_config and self._has_gpu():
            result = self._retrain_lora()
        else:
            result = self._retrain_mlp()

        if result.success:
            self._last_retrain_count = total_entombed
            self._last_retrain_time = time.time()
            self._save_state()

        result.samples_used = counts.get("sequences", 0)
        result.elapsed_seconds = time.time() - start_time
        return result

    def _retrain_mlp(self) -> RetrainResult:
        """Retrain BinaryMLP on CPU."""
        try:
            from redclaw.training.train_predictor import train
            metrics = train(
                data_dir=self.data_dir,
                epochs=50,
                batch_size=32,
                lr=1e-3,
            )
            accuracy = metrics.get("best_eval_acc", 0.0)
            return RetrainResult(
                model_type="mlp",
                success=True,
                accuracy=accuracy,
                message=f"BinaryMLP retrained: {accuracy:.1%} accuracy",
            )
        except Exception as e:
            return RetrainResult("mlp", False, message=f"MLP retrain failed: {e}")

    def _retrain_lora(self) -> RetrainResult:
        """Retrain BitNet LoRA on GPU."""
        try:
            # Prepare data
            from redclaw.training.prepare_bitnet_data import prepare_bitnet_dataset
            bitnet_dir = self.data_dir / "bitnet"
            counts = prepare_bitnet_dataset(self.data_dir, bitnet_dir)

            if counts["total"] < 100:
                return RetrainResult(
                    "lora", False,
                    message=f"Insufficient data for LoRA: {counts['total']} samples",
                )

            # Train
            from redclaw.training.train_bitnet import train_bitnet_lora
            config = self.lora_config
            results = train_bitnet_lora(
                base_model_path=config.base_model_path,
                data_dir=bitnet_dir,
                output_dir=self.data_dir / "bitnet_finetuned",
                epochs=config.epochs,
                batch_size=config.batch_size,
                lr=config.lr,
                rank=config.rank,
                alpha=config.alpha,
            )

            accuracy = 1.0 - results.get("best_val_loss", 10.0) / 10.0  # Rough metric

            return RetrainResult(
                model_type="lora",
                success=True,
                accuracy=max(0, accuracy),
                message=f"LoRA retrained: val_loss={results.get('best_val_loss', 'N/A')}",
            )
        except Exception as e:
            logger.warning("LoRA retrain failed, falling back to MLP: %s", e)
            return self._retrain_mlp()

    def _has_gpu(self) -> bool:
        """Check if CUDA GPU is available."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False
