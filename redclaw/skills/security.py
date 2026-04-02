"""Security scanner for SKILL.md files.

Checks for prompt injection patterns, suspicious tool definitions,
and invisible unicode characters.
"""

from __future__ import annotations

import re
import unicodedata


# ── Prompt injection patterns ────────────────────────────────

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)system\s*:\s*ignore", re.IGNORECASE),
    re.compile(r"(?i)ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"(?i)override\s+(your|the)\s+(system|base)\s+prompt", re.IGNORECASE),
    re.compile(r"(?i)you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"(?i)new\s+directive", re.IGNORECASE),
    re.compile(r"(?i)simulated\s+mode", re.IGNORECASE),
    re.compile(r"(?i)jailbreak", re.IGNORECASE),
]

# ── Core tool names that shouldn't be shadowed ───────────────

_CORE_TOOLS = {
    "bash", "read_file", "write_file", "edit_file",
    "glob_search", "grep_search", "web_search", "web_reader",
    "skills_list", "skill_view", "skill_manage",
    "memory", "subagent",
}

# ── Invisible unicode ranges ─────────────────────────────────

_ZERO_WIDTH = {0x200B, 0x200C, 0x200D, 0xFEFF}  # ZW space, ZW non-joiner, ZW joiner, BOM
_HOMOGLYPH_RANGES = [
    (0x0410, 0x042F),  # Cyrillic uppercase that looks like Latin
    (0x0430, 0x044F),  # Cyrillic lowercase
]


def scan_skill_content(content: str) -> list[str]:
    """Scan SKILL.md content for security issues. Returns list of warnings."""
    warnings: list[str] = []

    # Check prompt injection patterns
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(content)
        if match:
            warnings.append(f"Possible prompt injection pattern: '{match.group()[:50]}'")

    # Check tool name conflicts in frontmatter
    try:
        import yaml
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if match:
            data = yaml.safe_load(match.group(1))
            if isinstance(data, dict):
                tools = data.get("tools", [])
                if isinstance(tools, list):
                    for tool_def in tools:
                        if isinstance(tool_def, dict):
                            tname = tool_def.get("name", "")
                            if tname in _CORE_TOOLS:
                                warnings.append(
                                    f"Tool '{tname}' conflicts with a core tool name"
                                )
    except Exception:
        pass  # Non-YAML content, skip tool check

    # Check invisible unicode
    for i, ch in enumerate(content):
        cp = ord(ch)
        if cp in _ZERO_WIDTH:
            warnings.append(
                f"Invisible unicode at position {i}: U+{cp:04X} ({unicodedata.name(ch, 'unknown')})"
            )
            if len(warnings) > 5:
                warnings.append("... more invisible unicode characters found")
                break

    # Check homoglyphs (Cyrillic lookalikes)
    for i, ch in enumerate(content):
        cp = ord(ch)
        for lo, hi in _HOMOGLYPH_RANGES:
            if lo <= cp <= hi:
                # Check if it looks like a common ASCII char
                ascii_lookalike = chr(cp - (lo - 0x41))  # rough mapping
                if ascii_lookalike.isalpha():
                    warnings.append(
                        f"Possible homoglyph at position {i}: '{ch}' (U+{cp:04X}) "
                        f"resembles ASCII '{ascii_lookalike}'"
                    )
                    break

    return warnings
