"""Skill discovery and loading from YAML manifests or SKILL.md files."""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
from pathlib import Path
from typing import Any

from redclaw.skills.base import SkillBase, SkillManifest, SkillTool

logger = logging.getLogger(__name__)


def _parse_manifest(yaml_path: Path) -> SkillManifest | None:
    """Parse a skill.yaml manifest file."""
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed. Skills system unavailable.")
        return None

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to parse {yaml_path}: {e}")
        return None

    if not data or "name" not in data:
        logger.error(f"Invalid skill.yaml: {yaml_path}")
        return None

    return SkillManifest(
        name=data["name"],
        description=data.get("description", ""),
        version=data.get("version", "1.0"),
        tools=data.get("tools", []),
    )


def _parse_skill_md(md_path: Path) -> SkillManifest | None:
    """Parse a SKILL.md file with YAML frontmatter + markdown body."""
    try:
        raw = md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read {md_path}: {e}")
        return None

    # Extract YAML frontmatter between --- delimiters
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", raw, re.DOTALL)
    if not match:
        logger.error(f"SKILL.md missing frontmatter: {md_path}")
        return None

    frontmatter_str, body = match.group(1), match.group(2)

    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed. Cannot parse SKILL.md frontmatter.")
        return None

    try:
        data = yaml.safe_load(frontmatter_str)
    except Exception as e:
        logger.error(f"Failed to parse SKILL.md frontmatter in {md_path}: {e}")
        return None

    if not data or "name" not in data:
        logger.error(f"Invalid SKILL.md frontmatter (missing 'name'): {md_path}")
        return None

    return SkillManifest(
        name=data["name"],
        description=data.get("description", ""),
        version=data.get("version", "1.0"),
        tools=data.get("tools", []),
        instructions=body.strip(),
    )


def _detect_manifest(skill_dir: Path) -> SkillManifest | None:
    """Detect and parse manifest from skill_dir (SKILL.md or skill.yaml)."""
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file():
        return _parse_skill_md(skill_md)

    yaml_path = skill_dir / "skill.yaml"
    if yaml_path.is_file():
        return _parse_manifest(yaml_path)

    return None


def _load_skill_module(skill_dir: Path, manifest: SkillManifest) -> SkillBase | None:
    """Load the Python module for a skill."""
    # Look for a .py file matching the skill name
    module_path = skill_dir / f"{manifest.name}.py"
    if not module_path.is_file():
        # Try index.py
        module_path = skill_dir / "index.py"
        if not module_path.is_file():
            logger.warning(f"No Python module found for skill '{manifest.name}' in {skill_dir}")
            return None

    try:
        spec = importlib.util.spec_from_file_location(
            f"redclaw.skills.{manifest.name}",
            str(module_path),
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error(f"Failed to load skill module {module_path}: {e}")
        return None

    # Look for a class that inherits from SkillBase
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, SkillBase)
            and attr is not SkillBase
        ):
            try:
                skill = attr(manifest, str(skill_dir))
                return skill
            except Exception as e:
                logger.error(f"Failed to instantiate skill class {attr_name}: {e}")
                return None

    # If no SkillBase subclass found, create a basic skill from the manifest tools
    return _create_basic_skill(manifest, module, str(skill_dir))


def _create_basic_skill(manifest: SkillManifest, module: Any, skill_dir: str) -> SkillBase | None:
    """Create a basic skill from manifest tools + module functions."""
    skill = SkillBase(manifest, skill_dir)

    for tool_def in manifest.tools:
        tool_name = tool_def.get("name", "")
        if not tool_name:
            continue

        func = getattr(module, tool_name, None)
        if func is None:
            logger.warning(f"Tool function '{tool_name}' not found in module for skill '{manifest.name}'")
            continue

        # Wrap sync functions as async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            execute = func
        else:
            async def _wrap(f=func, **kw):
                return f(**kw)
            execute = _wrap

        skill.add_tool(
            name=tool_name,
            description=tool_def.get("description", ""),
            parameters=tool_def.get("parameters", {"type": "object", "properties": {}}),
            execute=execute,
        )

    return skill


def discover_skills(skills_dirs: list[str] | None = None) -> list[SkillBase]:
    """Discover and load all skills from the given directories."""
    skills: list[SkillBase] = []
    search_dirs: list[Path] = []

    # Default search paths
    for d in (skills_dirs or []):
        search_dirs.append(Path(d))
    # Also search in <cwd>/skills/ and <package_dir>/skills/
    search_dirs.append(Path.cwd() / "skills")
    # User home skills directory
    search_dirs.append(Path.home() / ".redclaw" / "skills")

    seen_names: set[str] = set()

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for skill_dir in sorted(search_dir.iterdir()):
            if not skill_dir.is_dir():
                continue

            manifest = _detect_manifest(skill_dir)
            if manifest is None:
                continue

            if manifest.name in seen_names:
                continue
            seen_names.add(manifest.name)

            skill = _load_skill_module(skill_dir, manifest)
            if skill is not None:
                skills.append(skill)
                logger.info(f"Loaded skill: {manifest.name} ({len(skill.tools)} tools)")

    return skills


def register_skill_tools(skills: list[SkillBase], tool_executor: Any) -> None:
    """Register all skill tools with the ToolExecutor."""
    from redclaw.api.types import PermissionLevel
    from redclaw.tools.registry import ToolSpec

    for skill in skills:
        for tool in skill.tools:
            spec = ToolSpec(
                name=tool.name,
                description=tool.description,
                input_schema=tool.parameters,
                permission=PermissionLevel.READ_ONLY,
                execute=tool.execute,
            )
            tool_executor.specs[tool.name] = spec
            logger.debug(f"Registered skill tool: {tool.name}")
