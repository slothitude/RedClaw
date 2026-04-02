# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RedClaw is a minimal AI coding agent with multiple interfaces (CLI REPL, Godot 4.6 GUI via JSON-RPC, Telegram bot, WebChat). It's provider-agnostic — supports OpenAI, Anthropic, Ollama, Groq, DeepSeek, OpenRouter, and custom LLM providers through a unified adapter layer.

## Commands

```bash
pip install -e .              # Install
pip install -e ".[dev]"       # Install with dev deps (pytest, pytest-asyncio)
python -m redclaw             # Run CLI REPL
pytest                        # Run tests
```

No linter or formatter is configured.

## Architecture

The codebase is organized in layers with clear data flow:

```
Interfaces (cli.py, rpc.py, telegram_bot.py, webchat.py)
    ↓
Runtime (redclaw/runtime/)
    conversation.py → core agent loop: stream API → execute tools → loop
    session.py      → JSONL conversation persistence
    compact.py      → conversation compaction/summarization
    permissions.py  → 4-tier policy: ask, read_only, workspace_write, danger_full_access
    ↓
API Layer (redclaw/api/)
    client.py    → async HTTP client with SSE streaming + retry (exponential backoff on 429s)
    providers.py → adapter registry mapping provider names → base_url, auth headers, message format
    types.py     → Message, ToolDefinition, Usage, StreamEvent dataclasses
    sse.py       → SSE parser for streaming responses
    ↓
Tools (redclaw/tools/)
    registry.py → ToolExecutor dispatches tool calls
    bash.py, file_ops.py, search.py → 6 core tools: bash, read_file, write_file, edit_file, glob_search, grep_search
```

### Key patterns

- **All I/O is async** — httpx for HTTP, asyncio.subprocess for bash, aiohttp for WebChat
- **Streaming** — LLM responses stream via SSE; interfaces receive callbacks (`ConversationCallbacks`: `on_text_delta`, `on_tool_begin`, `on_tool_result`)
- **Provider abstraction** — Each provider is a `ProviderConfig` with `message_format` ("openai" or "anthropic"); the client adapts request/response shapes accordingly
- **Session persistence** — Conversations saved as JSONL in the working directory
- **Extensibility** — Skills system (`redclaw/skills/`, YAML+Python plugins) and MCP client (`redclaw/mcp_client.py`, SSE protocol for external tool servers)

### Entry points

- CLI: `python -m redclaw` → `__main__.py:main` → `cli.py` (argparse + REPL loop)
- Godot: `--mode rpc` → `rpc.py` (JSON-RPC over stdio)
- Telegram: `--mode telegram` → `telegram_bot.py`
- WebChat: `--mode webchat` → `webchat.py` (aiohttp server)
- Script entry: `redclaw` console script → `redclaw.__main__:main`

### CLI flags

Key flags: `--provider`, `--model`, `--base-url`, `--permission-mode`, `--session`, `--working-dir`, `--mode`, `--mcp-servers`, `--tts-url`, `--stt-url`, `--search-url`

## Conventions

- Python 3.11+ with `from __future__ import annotations` in most modules
- Dataclasses for all data types (no Pydantic)
- `logging` module throughout — no print statements in library code
- Tools return string results; errors surfaced via `is_error=True` in `ToolResultBlock`
- The Godot GUI project lives in `godot/` with GDScript in `godot/scripts/` and `godot/ui/`
