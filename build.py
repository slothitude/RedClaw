#!/usr/bin/env python
"""Build RedClaw into a standalone Windows exe using PyInstaller."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INIT_PY = ROOT / "redclaw" / "__init__.py"


def get_pyproject_version() -> str:
    """Extract version from pyproject.toml."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', text)
    if not m:
        print("ERROR: could not find version in pyproject.toml")
        sys.exit(1)
    return m.group(1)


def bump_init(version: str) -> None:
    """Update __version__ in __init__.py to match pyproject.toml."""
    text = INIT_PY.read_text(encoding="utf-8")
    new_text = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', text)
    if new_text != text:
        INIT_PY.write_text(new_text, encoding="utf-8")
        print(f"Bumped __init__.py to {version}")
    else:
        print(f"__init__.py already at {version}")


def main() -> None:
    version = get_pyproject_version()
    print(f"Building RedClaw v{version}")

    bump_init(version)

    spec = ROOT / "redclaw.spec"
    if not spec.exists():
        print("ERROR: redclaw.spec not found")
        sys.exit(1)

    print("Running PyInstaller...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(spec), "--noconfirm"],
        cwd=str(ROOT),
    )

    if result.returncode != 0:
        print("ERROR: PyInstaller failed")
        sys.exit(result.returncode)

    exe_path = ROOT / "dist" / "redclaw.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\nBuild successful: {exe_path} ({size_mb:.1f} MB)")
    else:
        print(f"\nWARNING: expected {exe_path} not found")


if __name__ == "__main__":
    main()
