"""Feature encoding for ToolCallPredictor.

Converts variable-length tool sequences into fixed-size tensors using
a sliding window approach over tool call histories.

Input features per step (47 dims):
  - Tool history: last N tools as one-hot (N=5, vocab_size=8) → 40 dims
  - Position in sequence: normalized float → 1 dim
  - Repo type: one-hot → 5 dims
  - Call count so far: integer → 1 dim
"""

from __future__ import annotations

from pathlib import Path

import json
import torch
from torch import Tensor

TOOL_VOCAB = ["bash", "read_file", "write_file", "edit_file", "glob_search", "grep_search", "web_search", "web_reader"]
TOOL_TO_IDX = {name: i for i, name in enumerate(TOOL_VOCAB)}

REPO_TYPES = ["django", "matplotlib", "sympy", "sphinx", "other"]
REPO_TO_IDX = {name: i for i, name in enumerate(REPO_TYPES)}

WINDOW_SIZE = 5
INPUT_SIZE = len(TOOL_VOCAB) * WINDOW_SIZE + 1 + len(REPO_TYPES) + 1  # 40 + 1 + 5 + 1 = 47


def _get_repo_type(instance_id: str) -> str:
    """Extract repo type from instance_id (e.g. 'django__django-14382' → 'django')."""
    lower = instance_id.lower()
    for repo in REPO_TYPES[:-1]:
        if repo in lower:
            return repo
    return "other"


def encode_sequence(
    tool_names: list[str],
    instance_id: str,
    window: int = WINDOW_SIZE,
) -> list[tuple[Tensor, Tensor]]:
    """Encode a tool sequence into (input, label) pairs for training.

    Each pair represents: given the history so far, predict the next tool.
    """
    pairs: list[tuple[Tensor, Tensor]] = []
    seq_len = len(tool_names)
    repo_type = _get_repo_type(instance_id)

    for pos in range(seq_len - 1):
        # ── Tool history: last `window` tools, one-hot encoded ──
        history = torch.zeros(window, len(TOOL_VOCAB))
        for i in range(window):
            lookback_pos = pos - window + 1 + i
            if lookback_pos >= 0:
                tool = tool_names[lookback_pos]
                if tool in TOOL_TO_IDX:
                    history[i, TOOL_TO_IDX[tool]] = 1.0
        history_flat = history.flatten()  # (window * vocab_size,)

        # ── Position: normalized 0-1 ──
        position = torch.tensor([pos / max(seq_len - 1, 1)], dtype=torch.float32)

        # ── Repo type: one-hot ──
        repo_onehot = torch.zeros(len(REPO_TYPES))
        repo_onehot[REPO_TO_IDX[repo_type]] = 1.0

        # ── Call count so far ──
        count = torch.tensor([pos + 1], dtype=torch.float32)

        # ── Assemble input ──
        x = torch.cat([history_flat, position, repo_onehot, count])

        # ── Label: next tool ──
        next_tool = tool_names[pos + 1]
        label = torch.tensor(TOOL_TO_IDX.get(next_tool, 0), dtype=torch.long)

        pairs.append((x, label))

    return pairs


def load_sequences(data_dir: Path) -> list[dict]:
    """Load sequences.jsonl and return list of dicts."""
    sequences_path = data_dir / "sequences.jsonl"
    if not sequences_path.is_file():
        raise FileNotFoundError(f"No sequences.jsonl in {data_dir}")

    sequences = []
    with open(sequences_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                sequences.append(json.loads(line))
    return sequences


def encode_all_sequences(
    data_dir: Path,
    window: int = WINDOW_SIZE,
    successes_only: bool = False,
    surgical_weight: float = 2.0,
    max_surgical_calls: int = 15,
) -> list[tuple[Tensor, Tensor, Tensor]]:
    """Load sequences.jsonl and encode all into (input, label, weight) triples.

    Args:
        successes_only: If True, only use successful sequences.
        surgical_weight: Weight multiplier for surgical (few tool calls) successes.
        max_surgical_calls: Threshold for "surgical" trajectories.
    """
    sequences = load_sequences(data_dir)
    all_triples: list[tuple[Tensor, Tensor, Tensor]] = []

    for seq in sequences:
        tool_names = seq.get("tool_names", [])
        instance_id = seq.get("instance_id", "")
        success = seq.get("success", False)
        tool_calls = seq.get("tool_calls", len(tool_names))

        if len(tool_names) < 2:
            continue
        if successes_only and not success:
            continue

        pairs = encode_sequence(tool_names, instance_id, window)

        # Weight: surgical successes get boosted, failures get reduced
        if success and tool_calls <= max_surgical_calls:
            w = surgical_weight  # surgical success → high weight
        elif success:
            w = 1.0  # non-surgical success → normal weight
        else:
            w = 0.3  # failure → low weight

        for x, y in pairs:
            all_triples.append((x, y, torch.tensor(w, dtype=torch.float32)))

    return all_triples
