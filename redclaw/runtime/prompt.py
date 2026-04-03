"""System prompt builder — CLAW.md discovery, git context, working directory."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def build_system_prompt(
    working_dir: str | None = None,
    extra_instructions: str = "",
    memory_snapshot: str = "",
    skills_guidance: bool = False,
    mode: str = "coder",
    assistant_context: str = "",
) -> str:
    """Build the system prompt with context."""
    cwd = working_dir or str(Path.cwd())
    parts: list[str] = []

    # Base identity
    # Extract persona name from assistant_context if present
    name = "RedClaw"
    if assistant_context:
        for line in assistant_context.split("\n"):
            if line.startswith("Your name is ") and line.endswith("."):
                name = line[len("Your name is "):-1]
                break
    if mode == "assistant":
        parts.append(
            f"You are {name}, a proactive personal assistant. You help users manage their tasks, "
            "reminders, notes, and daily life. You can also help with coding, web research, and "
            "general questions. Be concise, friendly, and helpful.\n"
        )
    else:
        parts.append(
            "You are RedClaw, an AI coding agent. You help users with software engineering tasks "
            "by reading files, searching code, writing/editing files, and running commands.\n"
        )

    # Working directory
    parts.append(f"Working directory: {cwd}")

    # Git context
    git_info = _get_git_context(cwd)
    if git_info:
        parts.append(f"Git: {git_info}")

    # CLAW.md instructions
    claw_md = _read_claw_md(cwd)
    if claw_md:
        parts.append(f"\nProject instructions (CLAW.md):\n{claw_md}")

    # Extra instructions
    if extra_instructions:
        parts.append(f"\nAdditional instructions:\n{extra_instructions}")

    # Assistant context (current time, pending tasks, etc.)
    if assistant_context:
        parts.append(f"\nCurrent context:\n{assistant_context}")

    # Memory snapshot
    if memory_snapshot:
        parts.append(f"\nMemory (frozen snapshot for this session):\n{memory_snapshot}")

    # Skills guidance
    if skills_guidance:
        parts.append(
            "\nSkill management:\n"
            "- After completing complex multi-step tasks, consider creating a reusable skill.\n"
            "- Use skills_list to see existing skills, skill_view to inspect them.\n"
            "- Use skill_manage to create, update, patch, delete, evolve, or record usage.\n"
            "- Use skill_manage with action='record_usage' after each skill invocation to track reliability.\n"
            "- Use skill_manage with action='evolve' to auto-improve low-performing skills.\n"
            "- Skills are stored as SKILL.md files in ~/.redclaw/skills/<name>/.\n"
        )

        # Inject skill experience from metrics
        experience = _skills_experience_block()
        if experience:
            parts.append(experience)

    # Tool usage guidelines
    parts.append(
        "\nGuidelines:\n"
        "- Read files before editing them.\n"
        "- Use glob_search to find files by name pattern.\n"
        "- Use grep_search to find code by content.\n"
        "- Use edit_file for targeted changes (prefer over write_file for existing files).\n"
        "- Run bash commands for git, tests, builds, and other operations.\n"
        "- Be concise. Explain your changes briefly.\n"
    )

    return "\n".join(parts)


def _get_git_context(cwd: str) -> str | None:
    """Get git branch and status info."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if branch.returncode != 0:
            return None
        branch_name = branch.stdout.strip()

        # Check for uncommitted changes
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        dirty = bool(status.stdout.strip())
        return f"branch={branch_name}, {'dirty' if dirty else 'clean'}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _read_claw_md(cwd: str) -> str | None:
    """Read CLAW.md or .claw.md from the working directory or its parents."""
    for name in ("CLAW.md", ".claw.md"):
        # Check current dir
        path = Path(cwd) / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")

    # Check parent directories up to home
    current = Path(cwd).resolve()
    home = Path.home()
    while current != home and current.parent != current:
        current = current.parent
        for name in ("CLAW.md", ".claw.md"):
            path = current / name
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")

    return None


def _skills_experience_block() -> str:
    """Read skill metrics and return guidance about reliability."""
    import json

    skills_dir = Path.home() / ".redclaw" / "skills"
    if not skills_dir.is_dir():
        return ""

    entries: list[str] = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        metrics_path = skill_dir / ".metrics.json"
        if not metrics_path.is_file():
            continue
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        usage = metrics.get("usage_count", 0)
        if usage == 0:
            continue

        success = metrics.get("success_count", 0)
        rate = (success / usage) * 100
        name = skill_dir.name

        if rate >= 80:
            entries.append(f"- Skill '{name}': {rate:.0f}% success ({usage} uses) — high reliability, trust it")
        elif rate >= 50:
            entries.append(f"- Skill '{name}': {rate:.0f}% success ({usage} uses) — moderate, verify results")
        else:
            entries.append(f"- Skill '{name}': {rate:.0f}% success ({usage} uses) — LOW reliability, consider alternatives or use skill_manage evolve")

    if not entries:
        return ""

    return "\nSkill experience (based on past usage):\n" + "\n".join(entries)
