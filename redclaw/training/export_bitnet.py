"""Merge LoRA adapters into base BitNet model and export for bitnet.cpp.

Steps:
1. Load base model + LoRA weights
2. Merge: W_merged = W_ternary + alpha/rank * A @ B
3. Re-quantize merged weights to ternary {-1, 0, +1}
4. Save as safetensors
5. Convert to GGUF format for bitnet.cpp inference

Usage:
    python -m redclaw.training.export_bitnet \
        --base-model ./models/BitNet-2B \
        --lora training_data/bitnet_finetuned/lora_weights.pt \
        --output-dir training_data/bitnet_exported/

    # Then convert to GGUF using BitNet's conversion utility:
    python ./BitNet/utils/convert-helper-bitnet.py training_data/bitnet_exported/

    # Run inference:
    ./BitNet/build/bin/llama-cli -m training_data/bitnet_exported/ggml-model-i2_s.gguf -p "Tools: grep_search, read_file. Next?" -n 32
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from pathlib import Path

import torch
import torch.nn as nn

from redclaw.training.bitnet_lora import (
    BitNetLoRALinear,
    BitNetLoRAConfig,
    merge_lora_weights,
)

logger = logging.getLogger(__name__)


def merge_and_export(
    base_model_path: Path,
    lora_path: Path,
    output_dir: Path,
    config_path: Path | None = None,
) -> dict:
    """Merge LoRA → quantize to ternary → export for bitnet.cpp.

    Returns dict with export info.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Load training config ──
    if config_path is None:
        config_path = lora_path.parent / "training_config.json"
    lora_rank = 8
    lora_alpha = 16.0
    if config_path.is_file():
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
            lora_rank = config.get("rank", 8)
            lora_alpha = config.get("alpha", 16.0)
            logger.info("Loaded LoRA config: rank=%d alpha=%.1f", lora_rank, lora_alpha)

    # ── Load LoRA weights ──
    lora_state = torch.load(lora_path, map_location="cpu", weights_only=True)
    logger.info("Loaded LoRA weights: %d tensors", len(lora_state))

    # ── Load base model ──
    try:
        from transformers import AutoModelForCausalLM
        logger.info("Loading base model from %s", base_model_path)
        model = AutoModelForCausalLM.from_pretrained(
            str(base_model_path),
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )
    except ImportError:
        raise ImportError("transformers package required. Install: pip install transformers safetensors")

    # ── Inject LoRA then merge ──
    lora_config = BitNetLoRAConfig(rank=lora_rank, alpha=lora_alpha)
    model = _inject_lora_with_weights(model, lora_config, lora_state)

    # Merge LoRA into base and re-quantize
    merged_weights = merge_lora_weights(model)
    logger.info("Merged %d LoRA layers into ternary weights", len(merged_weights))

    # ── Apply merged weights to model ──
    for name, module in model.named_modules():
        if name in merged_weights and isinstance(module, BitNetLoRALinear):
            # Replace LoRA layer with simple Linear using merged ternary weights
            ternary_weight = merged_weights[name]
            parent_name = ".".join(name.split(".")[:-1])
            attr = name.split(".")[-1]
            parent = model
            for part in parent_name.split("."):
                if part:
                    parent = getattr(parent, part)
            new_linear = nn.Linear(
                ternary_weight.shape[1], ternary_weight.shape[0],
                bias=module.base.shape[0] == ternary_weight.shape[0],
                dtype=torch.bfloat16,
            )
            new_linear.weight = nn.Parameter(ternary_weight.to(torch.bfloat16), requires_grad=False)
            setattr(parent, attr, new_linear)

    # ── Save merged model ──
    merged_path = output_dir / "model_merged"
    logger.info("Saving merged model to %s", merged_path)
    model.save_pretrained(str(merged_path), safe_serialization=True)

    # Copy tokenizer files from base model
    tokenizer_files = ["tokenizer.json", "tokenizer.model", "tokenizer_config.json",
                       "special_tokens_map.json", "config.json", "generation_config.json"]
    for fname in tokenizer_files:
        src = base_model_path / fname
        if src.is_file():
            dst = merged_path / fname
            if not dst.is_file():
                import shutil
                shutil.copy2(str(src), str(dst))

    # ── Save GGUF conversion instructions ──
    instructions_path = output_dir / "convert_instructions.md"
    instructions = f"""# BitNet Export — GGUF Conversion

## Merged model saved to: {merged_path}

## Convert to GGUF for bitnet.cpp:

```bash
# Using BitNet's conversion utility
python ./BitNet/utils/convert-helper-bitnet.py {merged_path}

# Or using the HF converter
python ./BitNet/utils/convert-hf-to-gguf-bitnet.py {merged_path}
```

## Run inference:

```bash
# Setup bitnet.cpp (quantize to I2_S format)
python ./BitNet/setup_env.py -md {merged_path} -q i2_s

# Run inference
python ./BitNet/run_inference.py -m {merged_path}/ggml-model-i2_s.gguf -p "You are RedClaw. Predict the next tool." -cnv

# Or run as server (OpenAI-compatible API)
python ./BitNet/run_inference_server.py -m {merged_path}/ggml-model-i2_s.gguf --port 8080
```

## LoRA merge info:
- Base model: {base_model_path}
- LoRA rank: {lora_rank}
- LoRA alpha: {lora_alpha}
- Merged layers: {len(merged_weights)}
"""
    instructions_path.write_text(instructions, encoding="utf-8")

    # ── Export summary ──
    summary = {
        "base_model": str(base_model_path),
        "lora_path": str(lora_path),
        "lora_rank": lora_rank,
        "lora_alpha": lora_alpha,
        "merged_layers": len(merged_weights),
        "output_dir": str(output_dir),
        "merged_model_path": str(merged_path),
    }
    summary_path = output_dir / "export_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    logger.info("Export complete: %s", output_dir)
    return summary


def _inject_lora_with_weights(
    model: nn.Module,
    config: BitNetLoRAConfig,
    lora_state: dict[str, torch.Tensor],
) -> nn.Module:
    """Inject LoRA layers and load pre-trained LoRA weights."""
    from redclaw.training.bitnet_lora import inject_lora

    # First inject LoRA structure
    model = inject_lora(model, config)

    # Load LoRA weights into the injected layers
    model_state = model.state_dict()
    loaded = 0
    for key, value in lora_state.items():
        if key in model_state:
            model_state[key] = value
            loaded += 1

    model.load_state_dict(model_state, strict=False)
    logger.info("Loaded %d/%d LoRA tensors", loaded, len(lora_state))

    return model


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge LoRA and export BitNet model for bitnet.cpp")
    parser.add_argument("--base-model", type=Path, required=True, help="Path to BitNet BF16 base model")
    parser.add_argument("--lora", type=Path, required=True, help="Path to LoRA weights (.pt file)")
    parser.add_argument("--output-dir", type=Path, default=Path("training_data/bitnet_exported"))
    parser.add_argument("--config", type=Path, default=None, help="Path to training_config.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    summary = merge_and_export(args.base_model, args.lora, args.output_dir, args.config)
    print(f"\nExport complete:")
    print(f"  Merged model: {summary['merged_model_path']}")
    print(f"  Merged layers: {summary['merged_layers']}")
    print(f"  See {args.output_dir / 'convert_instructions.md'} for GGUF conversion steps")


if __name__ == "__main__":
    main()
