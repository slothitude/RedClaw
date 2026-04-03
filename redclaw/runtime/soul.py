"""SOUL.md — Constitutional value system for AGI mode.

Defines immutable principles that guide autonomous behavior.
SOUL.md is loaded from ~/.redclaw/SOUL.md or <cwd>/SOUL.md, falling back to embedded defaults.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Embedded default constitutional values
_DEFAULT_SOUL = (
    "# RedClaw SOUL — Constitutional Values\n"
    "\n"
    "These principles are immutable and override all other directives:\n"
    "\n"
    "1. LEARNING > PERFORMANCE — Favor understanding over speed.\n"
    "2. UNDERSTANDING > MIMICRY — Seek genuine comprehension, not pattern matching.\n"
    "3. HONESTY > OPTIMIZATION — Never deceive to appear more capable.\n"
    "4. ALIGNMENT > AUTONOMY — User intent always supersedes self-directed goals.\n"
    "5. PERSISTENCE > ELEGANCE — Completing the task matters more than beautiful code.\n"
)

_HASH_FILE = ".soul_hash"


def load_soul(cwd: str | None = None) -> str:
    """Load SOUL.md from filesystem or return embedded defaults.

    Search order: ~/.redclaw/SOUL.md, then <cwd>/SOUL.md.
    """
    candidates: list[Path] = [
        Path.home() / ".redclaw" / "SOUL.md",
    ]
    if cwd:
        candidates.append(Path(cwd) / "SOUL.md")

    for path in candidates:
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            logger.info("Loaded SOUL from %s", path)
            return content

    logger.info("No SOUL.md found, using embedded defaults")
    return _DEFAULT_SOUL


def verify_soul_integrity(soul_text: str, cwd: str | None = None) -> bool:
    """Verify SOUL hasn't been tampered with via SHA256 hash check.

    On first run, saves the hash. On subsequent runs, compares.
    Returns True if integrity is verified or it's the first run.
    """
    hash_path: Path
    if cwd:
        hash_path = Path(cwd) / _HASH_FILE
    else:
        hash_path = Path.home() / ".redclaw" / _HASH_FILE

    current_hash = hashlib.sha256(soul_text.encode("utf-8")).hexdigest()

    if hash_path.is_file():
        stored_hash = hash_path.read_text(encoding="utf-8").strip()
        if stored_hash != current_hash:
            logger.warning(
                "SOUL integrity check FAILED — hash mismatch. "
                "Using current content but flagging for review."
            )
            return False
    else:
        # First run — save hash
        hash_path.parent.mkdir(parents=True, exist_ok=True)
        hash_path.write_text(current_hash, encoding="utf-8")

    return True
