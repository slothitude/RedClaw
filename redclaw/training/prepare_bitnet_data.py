"""Convert training sequences + patches to BitNet instruction-following format.

Three data types:
1. Tool call prediction: "Given these tools used so far, what's next?"
2. Surgical patch generation: "Fix this issue" -> actual diff
3. Workflow classification: "User wants X" -> tool sequence

Output format (Alpaca-style):
{
    "instruction": "...",
    "input": "...",
    "output": "..."
}

Usage:
    python -m redclaw.training.prepare_bitnet_data --data-dir training_data/ --output-dir training_data/bitnet/
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from pathlib import Path

from redclaw.training.encode import TOOL_VOCAB

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are RedClaw, an AI coding agent. You help users by using tools: " + ", ".join(TOOL_VOCAB) + "."


def _make_tool_prediction(
    tool_names: list[str],
    position: int,
) -> dict | None:
    """Create a tool call prediction sample.

    Given the tools used so far, predict the next tool.
    """
    if position >= len(tool_names) - 1:
        return None

    history = tool_names[:position + 1]
    next_tool = tool_names[position + 1]

    if next_tool not in TOOL_VOCAB:
        return None

    history_str = ", ".join(history) if history else "(none)"

    return {
        "instruction": f"{SYSTEM_PROMPT}\n\nGiven these tools used so far: [{history_str}]. Predict the next tool to use.",
        "input": f"Position {position + 1} in a {len(tool_names)}-step task.",
        "output": next_tool,
    }


def _make_patch_sample(
    instruction: str,
    output: str,
) -> dict:
    """Create a patch generation sample."""
    return {
        "instruction": f"{SYSTEM_PROMPT}\n\nGenerate a patch to fix the following issue:",
        "input": instruction[:500],
        "output": output[:2000],
    }


def _make_workflow_sample(
    instruction: str,
    tool_sequence: list[str],
    category: str,
) -> dict:
    """Create a workflow routing sample."""
    tools_str = " -> ".join(tool_sequence)
    return {
        "instruction": f"{SYSTEM_PROMPT}\n\nGiven a user request, determine the tool sequence to use.",
        "input": instruction[:500],
        "output": f"[{category}] {tools_str}",
    }


def _make_routing_sample(
    user_request: str,
    first_tool: str,
) -> dict:
    """Create a simple routing sample: user request -> first tool."""
    return {
        "instruction": f"{SYSTEM_PROMPT}\n\nGiven a user request, choose the first tool to use.",
        "input": user_request[:300],
        "output": first_tool,
    }


# Routing templates for generating routing training data
ROUTING_TEMPLATES = [
    ("Fix the bug in the authentication module", "grep_search"),
    ("Add a new endpoint for user registration", "glob_search"),
    ("Find where the database connection is configured", "grep_search"),
    ("Create a new configuration file", "write_file"),
    ("Update the README with new features", "read_file"),
    ("Debug the failing unit test", "bash"),
    ("Search for how to implement OAuth2", "web_search"),
    ("Read the API documentation for Stripe", "web_reader"),
    ("Find all Python files in the project", "glob_search"),
    ("Rename the function process_data to handle_data", "grep_search"),
    ("Check the git log for recent changes", "bash"),
    ("Look at the error in the log file", "read_file"),
    ("Set up the development environment", "bash"),
    ("Write tests for the new feature", "glob_search"),
    ("Deploy to staging server", "bash"),
    ("Review the code in the pull request", "read_file"),
    ("Add error handling to the API endpoint", "read_file"),
    ("Optimize the slow database query", "grep_search"),
    ("Update the dependencies", "bash"),
    ("Fix the security vulnerability in input validation", "grep_search"),
    ("Add a task to buy groceries", "task"),
    ("Set a reminder for the meeting tomorrow", "reminder"),
    ("Save a note about the project deadline", "note"),
    ("Search for information about Python asyncio", "web_search"),
    ("List all modified files", "bash"),
    ("Find the function that handles user login", "grep_search"),
    ("Check what version of Python is installed", "bash"),
    ("View the contents of the config file", "read_file"),
    ("Add a new route to the Flask app", "read_file"),
    ("Fix the import error in main.py", "read_file"),
]


def prepare_bitnet_dataset(
    data_dir: Path,
    output_dir: Path,
    val_split: float = 0.1,
    seed: int = 42,
) -> dict[str, int]:
    """Convert all training data to BitNet fine-tuning format.

    Returns: {"train": N, "val": M, "total": N+M}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    samples: list[dict] = []

    # ── Type 1: Tool call predictions from sequences.jsonl ──
    sequences_path = data_dir / "sequences.jsonl"
    if sequences_path.is_file():
        with open(sequences_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                seq = json.loads(line)
                tool_names = seq.get("tool_names", [])
                if len(tool_names) < 2:
                    continue
                for pos in range(len(tool_names) - 1):
                    sample = _make_tool_prediction(tool_names, pos)
                    if sample:
                        samples.append(sample)

    # ── Type 1b: Tool predictions from synthetic instructions ──
    synth_path = data_dir / "synthetic_instructions.jsonl"
    if synth_path.is_file():
        with open(synth_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                tool_seq = record.get("tool_sequence", [])
                if len(tool_seq) < 2:
                    continue
                for pos in range(len(tool_seq) - 1):
                    sample = _make_tool_prediction(tool_seq, pos)
                    if sample:
                        samples.append(sample)

    # ── Type 2: Patch generation from dataset.jsonl ──
    dataset_path = data_dir / "dataset.jsonl"
    if dataset_path.is_file():
        with open(dataset_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                instruction = entry.get("instruction", "")
                output = entry.get("output", "")
                if instruction and output:
                    samples.append(_make_patch_sample(instruction, output))

    # ── Type 3: Workflow routing from synthetic instructions ──
    if synth_path.is_file():
        with open(synth_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                instruction = record.get("instruction", "")
                tool_seq = record.get("tool_sequence", [])
                category = record.get("category", "unknown")
                if instruction and tool_seq:
                    samples.append(_make_workflow_sample(instruction, tool_seq, category))

    # ── Type 4: Simple routing from templates ──
    for user_req, first_tool in ROUTING_TEMPLATES:
        samples.append(_make_routing_sample(user_req, first_tool))

    # ── Shuffle and split ──
    rng.shuffle(samples)
    val_count = max(1, int(len(samples) * val_split))
    val_samples = samples[:val_count]
    train_samples = samples[val_count:]

    # ── Write output ──
    train_path = output_dir / "train.json"
    val_path = output_dir / "val.json"

    with open(train_path, "w", encoding="utf-8") as f:
        json.dump(train_samples, f, indent=2)

    with open(val_path, "w", encoding="utf-8") as f:
        json.dump(val_samples, f, indent=2)

    # ── Stats ──
    type_counts = {"tool_prediction": 0, "patch": 0, "workflow": 0, "routing": 0}
    for s in samples:
        inst = s["instruction"]
        if "Predict the next tool" in inst:
            type_counts["tool_prediction"] += 1
        elif "Generate a patch" in inst:
            type_counts["patch"] += 1
        elif "tool sequence to use" in inst:
            type_counts["workflow"] += 1
        elif "first tool to use" in inst:
            type_counts["routing"] += 1

    logger.info("Prepared %d samples: %s", len(samples), type_counts)
    logger.info("Train: %d, Val: %d", len(train_samples), len(val_samples))

    return {
        "train": len(train_samples),
        "val": len(val_samples),
        "total": len(samples),
        **type_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare BitNet instruction-following training data")
    parser.add_argument("--data-dir", type=Path, default=Path("training_data"))
    parser.add_argument("--output-dir", type=Path, default=Path("training_data/bitnet"))
    parser.add_argument("--val-split", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    counts = prepare_bitnet_dataset(args.data_dir, args.output_dir, args.val_split, args.seed)
    print(f"Prepared: {counts['train']} train, {counts['val']} val ({counts['total']} total)")
    print(f"  Tool predictions: {counts.get('tool_prediction', 0)}")
    print(f"  Patches: {counts.get('patch', 0)}")
    print(f"  Workflows: {counts.get('workflow', 0)}")
    print(f"  Routing: {counts.get('routing', 0)}")


if __name__ == "__main__":
    main()
