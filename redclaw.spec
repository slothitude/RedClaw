# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for RedClaw v1 — single-file Windows exe."""

import importlib.metadata
from PyInstaller.utils.hooks import copy_metadata

block_cipher = None

# --- version from package ---------------------------------------------------
try:
    version = importlib.metadata.version("redclaw")
except importlib.metadata.PackageNotFoundError:
    version = "1.0.0"

# --- collect metadata for packages that use it at runtime -------------------
datas = []
for pkg in ("flask", "httpx", "rich", "telegram", "aiohttp", "yaml"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

a = Analysis(
    ["redclaw/__main__.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # redclaw subpackages
        "redclaw",
        "redclaw.skills",
        "redclaw.skills.base",
        "redclaw.skills.loader",
        "redclaw.skills.agent_tools",
        "redclaw.skills.security",
        "redclaw.tools",
        "redclaw.tools.registry",
        "redclaw.tools.bash",
        "redclaw.tools.file_ops",
        "redclaw.tools.search",
        "redclaw.tools.memory",
        "redclaw.tools.toolsets",
        "redclaw.tools.content_scan",
        "redclaw.tools.assistant_tools",
        "redclaw.runtime",
        "redclaw.runtime.conversation",
        "redclaw.runtime.session",
        "redclaw.runtime.compact",
        "redclaw.runtime.permissions",
        "redclaw.runtime.prompt",
        "redclaw.runtime.hooks",
        "redclaw.runtime.subagent",
        "redclaw.runtime.subagent_types",
        "redclaw.runtime.usage",
        "redclaw.assistant",
        "redclaw.assistant.config",
        "redclaw.assistant.tasks",
        "redclaw.assistant.notes",
        "redclaw.assistant.reminders",
        "redclaw.memory_graph",
        "redclaw.channels",
        "redclaw.channels.base",
        "redclaw.channels.telegram",
        "redclaw.crypt",
        "redclaw.crypt.crypt",
        "redclaw.crypt.extractor",
        "redclaw.crypt.metrics",
        "redclaw.api",
        "redclaw.api.client",
        "redclaw.api.providers",
        "redclaw.api.types",
        "redclaw.api.sse",
        "redclaw.mcp_client",
        # dynamic dependencies
        "yaml",
        "httpx",
        "httpx_sse",
        "rich",
        "telegram",
        "telegram.ext",
        "aiohttp",
        "flask",
        "edge_tts",
        "anyio",
        "sniffio",
        "h11",
        "h2",
        "certifi",
        "idna",
        "charset_normalizer",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "whisper",
        "TTS",
        "playwright",
        "cognee",
        "numpy",
        "pandas",
        "scipy",
        "matplotlib",
        "PIL",
        "tkinter",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="redclaw",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version,
)
