"""Generate synthetic training data from workflow definitions.

Creates augmented tool sequences covering all 27 workflow categories
with controlled variation for robust training.

Usage:
    python -m redclaw.training.generate_dataset --output-dir training_data/ --variations 30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
from pathlib import Path

from redclaw.training.workflows import WORKFLOWS, Workflow, get_all_categories

logger = logging.getLogger(__name__)

# Extra tool variations that can appear in real workflows
OPTIONAL_TOOLS = {
    "bug_fix": ["bash", "glob_search"],
    "feature": ["bash", "grep_search", "glob_search"],
    "refactor": ["bash"],
    "exploration": [],
    "debugging": ["bash"],
    "git": ["read_file"],
    "web": ["grep_search"],
    "config": ["bash"],
    "testing": ["grep_search"],
    "deploy": ["read_file"],
    "assistant": [],
    "performance": ["read_file"],
    "security": ["bash"],
    "documentation": ["grep_search"],
}

# Realistic tool input templates for instruction generation
TOOL_TEMPLATES = {
    "grep_search": [
        "Find where {target} is defined",
        "Search for {target} in the codebase",
        "Locate all occurrences of {target}",
    ],
    "read_file": [
        "Read the file at {path}",
        "Check the contents of {path}",
        "View {path} to understand context",
    ],
    "edit_file": [
        "Apply fix in {path}",
        "Modify {path} to correct the issue",
        "Update {path} with the solution",
    ],
    "write_file": [
        "Create new file {path}",
        "Write {path} with initial content",
    ],
    "glob_search": [
        "Find files matching {pattern}",
        "Locate {pattern} files",
    ],
    "bash": [
        "Run tests",
        "Execute build",
        "Check git status",
        "Install dependencies",
    ],
    "web_search": [
        "Search for '{query}'",
        "Look up '{query}' online",
    ],
    "web_reader": [
        "Read the documentation at {url}",
        "Fetch the article about {topic}",
    ],
}

# Realistic targets/paths/queries for augmentation
AUGMENTATION_TARGETS = [
    "authenticate", "validate_input", "parse_config", "handle_error",
    "process_request", "format_output", "initialize_app", "connect_db",
    "cache_result", "serialize_data", "log_event", "send_notification",
]

AUGMENTATION_PATHS = [
    "src/main.py", "app/models.py", "lib/utils.py", "tests/test_main.py",
    "config/settings.py", "api/views.py", "core/engine.py", "handlers/auth.py",
]


def _pick_template(tool: str) -> str:
    """Pick a random instruction template for a tool."""
    templates = TOOL_TEMPLATES.get(tool, [f"Run {tool}"])
    return random.choice(templates)


def _fill_template(template: str) -> str:
    """Fill a template with realistic values."""
    result = template
    if "{target}" in result:
        result = result.replace("{target}", random.choice(AUGMENTATION_TARGETS))
    if "{path}" in result:
        result = result.replace("{path}", random.choice(AUGMENTATION_PATHS))
    if "{pattern}" in result:
        result = result.replace("{pattern}", random.choice(["*.py", "*.ts", "*.js", "*.yaml"]))
    if "{query}" in result:
        result = result.replace("{query}", random.choice(AUGMENTATION_TARGETS))
    if "{url}" in result:
        result = result.replace("{url}", "the documentation page")
    if "{topic}" in result:
        result = result.replace("{topic}", random.choice(AUGMENTATION_TARGETS))
    return result


def _augment_workflow(
    workflow: Workflow,
    variation_idx: int,
    seed: int,
) -> dict:
    """Create a single augmented variation of a workflow.

    Returns a dict with:
      - tool_names: list of tool names
      - success: bool
      - instance_id: string identifier
      - tool_calls: int
      - category: str
      - instruction: str (for BitNet training)
    """
    rng = random.Random(seed + variation_idx)
    steps = list(workflow.steps)

    # Sometimes add an optional tool step
    optional = OPTIONAL_TOOLS.get(workflow.category, [])
    if optional and rng.random() < 0.3:
        insert_pos = rng.randint(0, len(steps))
        extra_tool = rng.choice(optional)
        steps.insert(insert_pos, _make_step(extra_tool, rng))

    # Sometimes skip a non-critical step
    if len(steps) > 3 and rng.random() < 0.15:
        skip_pos = rng.randint(1, len(steps) - 2)
        steps.pop(skip_pos)

    tool_names = [s.tool for s in steps]

    # Determine success (weighted by workflow success rate)
    success = rng.random() < workflow.success_rate

    # Build instruction text
    task_desc = workflow.description
    tool_descriptions = []
    for s in steps:
        template = _pick_template(s.tool)
        tool_descriptions.append(_fill_template(template))

    instruction = f"{task_desc}. Steps: {'; '.join(tool_descriptions)}"

    # Unique ID
    id_hash = hashlib.md5(
        f"{workflow.name}:{variation_idx}:{seed}".encode()
    ).hexdigest()[:8]

    return {
        "tool_names": tool_names,
        "success": success,
        "instance_id": f"synth_{workflow.category}_{id_hash}",
        "tool_calls": len(tool_names),
        "category": workflow.category,
        "workflow_name": workflow.name,
        "instruction": instruction,
        "is_surgical": workflow.is_surgical,
    }


def _make_step(tool: str, rng: random.Random):
    """Create a synthetic WorkflowStep."""
    from redclaw.training.workflows import WorkflowStep
    desc = _fill_template(_pick_template(tool))
    return WorkflowStep(tool=tool, description=desc)


def generate_dataset(
    output_dir: Path,
    variations: int = 30,
    seed: int = 42,
) -> dict[str, int]:
    """Generate synthetic training data.

    Returns counts: {"sequences": N, "categories": M}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    sequences_path = output_dir / "sequences.jsonl"
    instructions_path = output_dir / "synthetic_instructions.jsonl"

    seq_count = 0
    inst_count = 0

    with open(sequences_path, "a", encoding="utf-8") as seq_f, \
         open(instructions_path, "w", encoding="utf-8") as inst_f:

        for workflow in WORKFLOWS:
            for v in range(variations):
                record = _augment_workflow(workflow, v, seed)

                # Write to sequences.jsonl (same format as export_dataset)
                seq_row = {
                    "tool_names": record["tool_names"],
                    "success": record["success"],
                    "instance_id": record["instance_id"],
                    "tool_calls": record["tool_calls"],
                }
                seq_f.write(json.dumps(seq_row) + "\n")
                seq_count += 1

                # Write to synthetic_instructions.jsonl (for BitNet)
                inst_row = {
                    "instruction": record["instruction"],
                    "tool_sequence": record["tool_names"],
                    "category": record["category"],
                    "workflow_name": record["workflow_name"],
                    "success": record["success"],
                }
                inst_f.write(json.dumps(inst_row) + "\n")
                inst_count += 1

    categories = get_all_categories()
    logger.info(
        "Generated %d sequences across %d categories (%d workflows x %d variations)",
        seq_count, len(categories), len(WORKFLOWS), variations,
    )

    return {"sequences": seq_count, "instructions": inst_count, "categories": len(categories)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic training data from workflow definitions")
    parser.add_argument("--output-dir", type=Path, default=Path("training_data"))
    parser.add_argument("--variations", type=int, default=30, help="Variations per workflow (default: 30)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    counts = generate_dataset(args.output_dir, args.variations, args.seed)
    print(f"Generated: {counts['sequences']} sequences, {counts['instructions']} instructions, {counts['categories']} categories")


if __name__ == "__main__":
    main()
