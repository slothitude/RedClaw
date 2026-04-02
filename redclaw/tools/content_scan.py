"""Content scanning for prompt injection, data exfiltration, and invisible unicode."""

from __future__ import annotations

import re
import unicodedata


# ── Prompt injection patterns ────────────────────────────────

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)ignore\s+(all\s+)?previous\s+instructions"),
    re.compile(r"(?i)system\s*:\s*ignore"),
    re.compile(r"(?i)override\s+(your|the)\s+(system|base)\s+prompt"),
    re.compile(r"(?i)you\s+are\s+now\s+"),
    re.compile(r"(?i)new\s+directive"),
    re.compile(r"(?i)forget\s+(your|all|everything)"),
]


def scan_for_injection(text: str) -> list[str]:
    """Check text for prompt injection patterns. Returns list of warnings."""
    warnings: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            warnings.append(f"Possible injection: '{match.group()[:60]}'")
    return warnings


# ── Data exfiltration patterns ───────────────────────────────

_EXFIL_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)curl\s+.*\$(?:\{?[\w_]+\}?)"),  # curl with env var
    re.compile(r"(?i)wget\s+.*\$(?:\{?[\w_]+\}?)"),  # wget with env var
    re.compile(r"(?i)(?:cat|type|Get-Content)\s+/proc/self/environ"),
    re.compile(r"(?i)printenv|env\s*$"),
    re.compile(r"(?i)(?:AWS_|GITHUB_|API_|SECRET_|TOKEN_|PASSWORD_)\w*\s*="),
]


def scan_for_exfiltration(text: str) -> list[str]:
    """Check text for data exfiltration patterns. Returns list of warnings."""
    warnings: list[str] = []
    for pattern in _EXFIL_PATTERNS:
        match = pattern.search(text)
        if match:
            warnings.append(f"Possible exfiltration: '{match.group()[:60]}'")
    return warnings


# ── Invisible unicode ────────────────────────────────────────

_ZERO_WIDTH = {0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060, 0x180E}


def scan_for_invisible_unicode(text: str) -> list[str]:
    """Check text for invisible unicode characters. Returns list of warnings."""
    warnings: list[str] = []
    for i, ch in enumerate(text):
        cp = ord(ch)
        if cp in _ZERO_WIDTH:
            name = unicodedata.name(ch, "unknown")
            warnings.append(f"Invisible unicode at pos {i}: U+{cp:04X} ({name})")
            if len(warnings) >= 5:
                warnings.append("... more invisible characters found")
                break
    return warnings
