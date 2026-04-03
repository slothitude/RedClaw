"""Agent-facing skill CRUD tools: skills_list, skill_view, skill_manage.

These tools let the agent create, read, update, and delete skills at runtime.
Skills are stored as SKILL.md files under ~/.redclaw/skills/<name>/.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from redclaw.skills.base import SkillManifest, load_skill_metrics, save_skill_metrics, record_skill_usage
from redclaw.skills.loader import _detect_manifest, _parse_skill_md
from redclaw.skills.security import scan_skill_content

logger = logging.getLogger(__name__)

_DEFAULT_SKILLS_DIR = Path.home() / ".redclaw" / "skills"


def _skills_dir(skills_base: str | None = None) -> Path:
    return Path(skills_base) if skills_base else _DEFAULT_SKILLS_DIR


# ── skills_list ──────────────────────────────────────────────

async def execute_skills_list(
    skills_base: str | None = None,
) -> str:
    """List all discovered skills with metadata."""
    base = _skills_dir(skills_base)
    if not base.is_dir():
        return "No skills found. Skills directory does not exist."

    entries: list[dict] = []
    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        manifest = _detect_manifest(skill_dir)
        if manifest:
            entries.append({
                "name": manifest.name,
                "description": manifest.description,
                "version": manifest.version,
                "has_instructions": bool(manifest.instructions),
                "tool_count": len(manifest.tools),
            })

    if not entries:
        return "No skills found."
    return json.dumps(entries, indent=2)


# ── skill_view ───────────────────────────────────────────────

async def execute_skill_view(
    name: str,
    detail: str = "metadata",
    skills_base: str | None = None,
) -> str:
    """Load skill content with progressive disclosure.

    detail: 'metadata' (name/desc/version), 'full' (entire SKILL.md), 'references' (tool list)
    """
    base = _skills_dir(skills_base)
    skill_dir = base / name
    if not skill_dir.is_dir():
        return f"Error: Skill '{name}' not found."

    manifest = _detect_manifest(skill_dir)
    if manifest is None:
        return f"Error: Skill '{name}' has no valid manifest."

    if detail == "metadata":
        return json.dumps({
            "name": manifest.name,
            "description": manifest.description,
            "version": manifest.version,
        }, indent=2)

    elif detail == "references":
        return json.dumps(manifest.tools, indent=2) if manifest.tools else "No tools defined."

    else:  # full
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            return skill_md.read_text(encoding="utf-8")
        yaml_path = skill_dir / "skill.yaml"
        if yaml_path.is_file():
            return yaml_path.read_text(encoding="utf-8")
        return json.dumps({
            "name": manifest.name,
            "description": manifest.description,
            "version": manifest.version,
            "instructions": manifest.instructions,
        }, indent=2)


# ── skill_manage ─────────────────────────────────────────────

async def execute_skill_manage(
    action: str,
    name: str,
    description: str = "",
    instructions: str = "",
    version: str = "1.0",
    skills_base: str | None = None,
) -> str:
    """Create, update, patch, or delete skills.

    action: 'create', 'update', 'patch', 'delete'
    """
    base = _skills_dir(skills_base)
    skill_dir = base / name

    if action == "delete":
        if not skill_dir.is_dir():
            return f"Error: Skill '{name}' not found."
        import shutil
        shutil.rmtree(skill_dir)
        return f"Deleted skill '{name}'."

    if action == "evolve":
        return await _evolve_skill(name, skill_dir)

    if action == "record_usage":
        if not skill_dir.is_dir():
            return f"Error: Skill '{name}' not found."
        success = description.lower() in ("true", "1", "yes", "success")
        record_skill_usage(skill_dir, success)
        return f"Recorded {'success' if success else 'failure'} for skill '{name}'."

    if action not in ("create", "update", "patch"):
        return f"Error: Unknown action '{action}'. Use create, update, patch, or delete."

    # Build SKILL.md content
    if action == "create":
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
            return f"Error: Skill '{name}' already exists. Use 'update' or 'patch'."
        skill_dir.mkdir(parents=True, exist_ok=True)
        content = _build_skill_md(name, description, version, instructions)
    elif action == "update":
        if not skill_dir.is_dir():
            return f"Error: Skill '{name}' not found. Use 'create' first."
        content = _build_skill_md(name, description, version, instructions)
    else:  # patch
        if not skill_dir.is_dir():
            return f"Error: Skill '{name}' not found. Use 'create' first."
        existing = _detect_manifest(skill_dir)
        if existing is None:
            return f"Error: Skill '{name}' has no valid manifest."
        desc = description or existing.description
        ver = version or existing.version
        instr = instructions or existing.instructions
        content = _build_skill_md(name, desc, ver, instr)

    # Security scan
    warnings = scan_skill_content(content)
    warn_text = ""
    if warnings:
        warn_text = "\n\nSecurity warnings:\n" + "\n".join(f"  - {w}" for w in warnings)

    # Atomic write
    skill_md_path = skill_dir / "SKILL.md"
    fd, tmp_path = tempfile.mkstemp(
        dir=str(skill_dir), prefix=".redclaw_", suffix=".md"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, skill_md_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    verb = {"create": "Created", "update": "Updated", "patch": "Patched"}[action]
    return f"{verb} skill '{name}'.{warn_text}"


def _build_skill_md(name: str, description: str, version: str, instructions: str) -> str:
    """Build a SKILL.md file from components."""
    import yaml
    frontmatter = yaml.dump({
        "name": name,
        "description": description,
        "version": version,
    }, default_flow_style=False).strip()
    body = instructions.strip()
    return f"---\n{frontmatter}\n---\n\n{body}\n" if body else f"---\n{frontmatter}\n---\n"


async def _evolve_skill(name: str, skill_dir: Path) -> str:
    """Auto-evolve a skill based on usage metrics.

    If success_rate < 50% and usage_count >= 5, appends a 'Lessons Learned'
    section to the skill instructions.
    """
    if not skill_dir.is_dir():
        return f"Error: Skill '{name}' not found."

    manifest = _detect_manifest(skill_dir)
    if manifest is None:
        return f"Error: Skill '{name}' has no valid manifest."

    metrics = load_skill_metrics(skill_dir)
    usage_count = metrics.get("usage_count", 0)
    success_count = metrics.get("success_count", 0)

    if usage_count == 0:
        return f"Skill '{name}' has no usage data. Nothing to evolve."

    success_rate = (success_count / usage_count) * 100

    if success_rate >= 50:
        return (
            f"Skill '{name}' is performing well ({success_rate:.0f}% success over "
            f"{usage_count} uses). No evolution needed."
        )

    if usage_count < 5:
        return (
            f"Skill '{name}' has only {usage_count} uses. Need at least 5 "
            f"before evolving (current success rate: {success_rate:.0f}%)."
        )

    # Append "Lessons Learned" section to the skill instructions
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lesson_block = (
        f"\n\n## Lessons Learned ({ts})\n\n"
        f"This skill has a {success_rate:.0f}% success rate over {usage_count} uses.\n"
        f"Consider refining the instructions to improve reliability.\n"
        f"- Review failed cases and add specific guidance for common pitfalls.\n"
        f"- Add preconditions or constraints that must be met before execution.\n"
        f"- Consider splitting complex tasks into smaller, more reliable steps.\n"
    )

    # Read existing SKILL.md and append
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file():
        content = skill_md.read_text(encoding="utf-8")
        if "## Lessons Learned" not in content:
            content = content.rstrip() + lesson_block
            # Atomic write
            fd, tmp_path = tempfile.mkstemp(
                dir=str(skill_dir), prefix=".redclaw_evolve_", suffix=".md"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, skill_md)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            return (
                f"Evolved skill '{name}'. Appended 'Lessons Learned' section. "
                f"Success rate: {success_rate:.0f}% ({success_count}/{usage_count})."
            )
        else:
            return (
                f"Skill '{name}' already has a 'Lessons Learned' section. "
                f"Consider using 'patch' to update instructions manually."
            )
    else:
        return f"Error: Skill '{name}' has no SKILL.md file to evolve."
