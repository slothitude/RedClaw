"""Export Crypt entombed records + experiment results into training datasets.

Produces two JSONL files:
  - dataset.jsonl: instruction-following format (for BitNet fine-tuning)
  - sequences.jsonl: tool-sequence format (for ToolCallPredictor)

Usage:
    python -m redclaw.training.export_dataset --results-dir docs/experiments --output-dir training_data/
"""

from __future__ import annotations

import json
import logging
import argparse
from pathlib import Path

logger = logging.getLogger(__name__)


def export(
    crypt_dir: Path | None = None,
    results_dir: Path | None = None,
    output_dir: Path | None = None,
    max_tool_calls: int = 15,
) -> dict[str, int]:
    """Export both dataset formats.

    Returns counts: {"dataset": N, "sequences": M}
    """
    crypt_dir = crypt_dir or Path.home() / ".redclaw" / "crypt"
    results_dir = results_dir or Path("docs/experiments")
    output_dir = output_dir or Path("training_data")
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Format A: Instruction-following JSONL ──────────────────
    dataset_path = output_dir / "dataset.jsonl"
    dataset_count = 0

    # Collect patches from experiment results (keyed by instance_id)
    patches: dict[str, str] = {}
    if results_dir.is_dir():
        for results_file in sorted(results_dir.glob("results_*.json")):
            try:
                entries = json.loads(results_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Skipping %s: %s", results_file, e)
                continue
            for entry in entries:
                iid = entry.get("instance_id", "")
                patch = entry.get("model_patch", "")
                if patch and entry.get("has_patch"):
                    # Keep the shorter patch if duplicates (less noise)
                    if iid not in patches or len(patch) < len(patches[iid]):
                        patches[iid] = patch

    # From entombed records
    entombed_dir = crypt_dir / "entombed"
    if entombed_dir.is_dir():
        for record_file in sorted(entombed_dir.glob("sub-*.json")):
            try:
                record = json.loads(record_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Skipping %s: %s", record_file, e)
                continue

            if not record.get("success"):
                continue
            if record.get("tool_calls", 0) > max_tool_calls:
                continue

            task = record.get("task", "").strip()
            if not task:
                continue

            # Try to find a matching patch from experiment results
            # Match by looking for instance_id patterns in the task string
            output = record.get("output_preview", "")
            for iid, patch in patches.items():
                if iid.replace("__", "/").replace("-", "/") in task or iid in task:
                    output = patch
                    break

            if not output:
                continue

            with open(dataset_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"instruction": task, "output": output}) + "\n")
            dataset_count += 1

    # From experiment results directly (if no entombed records)
    if dataset_count == 0:
        with open(dataset_path, "w", encoding="utf-8") as f:
            for iid, patch in sorted(patches.items()):
                task = f"Fix the issue described in {iid}"
                f.write(json.dumps({"instruction": task, "output": patch}) + "\n")
                dataset_count += 1
        logger.info("Exported %d entries from experiment results (no entombed records)", dataset_count)
    else:
        logger.info("Exported %d entries from entombed records", dataset_count)

    # ── Format B: Tool-sequence JSONL ──────────────────────────
    sequences_path = output_dir / "sequences.jsonl"
    sequences_count = 0

    if results_dir.is_dir():
        with open(sequences_path, "w", encoding="utf-8") as f:
            for results_file in sorted(results_dir.glob("results_*.json")):
                try:
                    entries = json.loads(results_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                for entry in entries:
                    tool_names = entry.get("tool_names", [])
                    if not tool_names:
                        continue
                    # Include both successful and failed for the predictor
                    row = {
                        "tool_names": tool_names,
                        "success": bool(entry.get("has_patch")),
                        "instance_id": entry.get("instance_id", ""),
                        "tool_calls": entry.get("tool_calls", len(tool_names)),
                    }
                    f.write(json.dumps(row) + "\n")
                    sequences_count += 1

    logger.info("Exported %d tool sequences", sequences_count)

    return {"dataset": dataset_count, "sequences": sequences_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export training datasets from Crypt and experiment data")
    parser.add_argument("--crypt-dir", type=Path, default=Path.home() / ".redclaw" / "crypt")
    parser.add_argument("--results-dir", type=Path, default=Path("docs/experiments"))
    parser.add_argument("--output-dir", type=Path, default=Path("training_data"))
    parser.add_argument("--max-tool-calls", type=int, default=15, help="Max tool calls for surgical trajectories")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    counts = export(args.crypt_dir, args.results_dir, args.output_dir, args.max_tool_calls)
    print(f"Exported: {counts['dataset']} instruction pairs, {counts['sequences']} tool sequences")


if __name__ == "__main__":
    main()
