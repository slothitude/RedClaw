# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RedClaw is a minimal AI coding agent with multiple interfaces (CLI REPL, Godot 4.6 GUI via JSON-RPC, Telegram bot, WebChat, Dashboard). It's provider-agnostic — supports OpenAI, Anthropic, Ollama, Groq, DeepSeek, OpenRouter, ZAI, and custom LLM providers through a unified adapter layer.

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
Interfaces (cli.py, rpc.py, telegram_bot.py, webchat.py, dashboard.py)
    ↓
Channels (channels/base.py, channels/telegram.py)
    Abstract message channel with ChannelMessage, ChannelConfig, ChannelBase
    ↓
Runtime (redclaw/runtime/)
    conversation.py → core agent loop: stream API → execute tools → loop
    session.py      → JSONL conversation persistence in .redclaw/ dir
    compact.py      → conversation compaction/summarization (deterministic or LLM-based)
    permissions.py  → 4-tier policy: ask, read_only, workspace_write, danger_full_access
    prompt.py       → system prompt builder: CLAW.md discovery, git context, memory snapshot
    hooks.py        → pre/post tool shell hooks (HookRunner, HookConfig)
    subagent.py     → isolated nested ConversationRuntime for subtasks
    subagent_types.py → SubagentType enum (CODER, SEARCHER, GENERAL) with typed prompts/toolsets
    usage.py        → token usage tracking with cost estimation
    ↓
Skills (redclaw/skills/)
    base.py       → SkillBase abstract class, SkillManifest, SkillTool dataclasses
    loader.py     → discovery: SKILL.md (YAML frontmatter + markdown) or skill.yaml
    agent_tools.py → agent-facing CRUD: skills_list, skill_view, skill_manage
    security.py   → security scanner for SKILL.md (injection, tool conflicts, homoglyphs)
    ↓
API Layer (redclaw/api/)
    client.py    → async HTTP client with SSE streaming + retry (exponential backoff on 429s)
    providers.py → adapter registry mapping provider names → base_url, auth headers, message format
    types.py     → Message, ToolDefinition, Usage, StreamEvent dataclasses
    sse.py       → SSE parser for streaming responses
    ↓
Tools (redclaw/tools/)
    registry.py     → ToolSpec dataclass, ToolExecutor dispatch, mvp_tool_specs
    bash.py         → bash command execution via asyncio subprocess
    file_ops.py     → read_file (line ranges), write_file (atomic), edit_file (exact string replace)
    search.py       → glob_search, grep_search, web_search (SearXNG), web_reader
    memory.py       → persistent memory with frozen snapshot pattern (MEMORY.md, USER.md)
    toolsets.py     → named toolsets with recursive include resolution
    content_scan.py → security scanning: prompt injection, data exfiltration, invisible unicode
    ↓
Crypt (redclaw/crypt/)
    crypt.py     → Crypt manager: bloodline wisdom, entombment, dharma
    extractor.py → lesson extraction from subagent results
    metrics.py   → CryptMetrics aggregate tracking + persistence
    ↓
MCP Client (redclaw/mcp_client.py)
    SSE-based protocol, persistent connections, JSON-RPC, tool discovery
    Tool registration with mcp__server__tool prefix convention
```

### Key patterns

- **All I/O is async** — httpx for HTTP, asyncio.subprocess for bash, aiohttp for WebChat
- **Streaming** — LLM responses stream via SSE; interfaces receive callbacks (`ConversationCallbacks`: `on_text_delta`, `on_tool_begin`, `on_tool_result`)
- **Provider abstraction** — Each provider is a `ProviderConfig` with `message_format` ("openai" or "anthropic"); the client adapts request/response shapes accordingly
- **Session persistence** — Conversations saved as JSONL in `.redclaw/` directory with metadata
- **Frozen memory snapshot** — MEMORY.md + USER.md loaded once at session start, injected into system prompt. Live mutations via tool calls persist to disk immediately but snapshot stays frozen (preserves prefix cache)
- **Extensibility** — Skills system (YAML+Python plugins), MCP client (SSE protocol for external tool servers), hooks (shell commands on tool events)
- **Security layers** — Permission tiers for tool access, content scanning (injection/exfiltration/unicode) on memory and skill content, skill security scanner for homoglyphs

### Entry points

- CLI: `python -m redclaw` → `__main__.py:main` → `cli.py` (argparse + REPL loop)
- Godot: `--mode rpc` → `rpc.py` (JSON-RPC over stdio)
- Telegram: `--mode telegram` → `telegram_bot.py` (per-user sessions, file upload/download)
- WebChat: `--mode webchat` → `webchat.py` (aiohttp server with embedded HTML UI)
- Dashboard: `--mode dashboard` → `dashboard.py` (Flask config GUI + process launcher)
- Script entry: `redclaw` console script → `redclaw.__main__:main`

### CLI flags

Key flags: `--provider`, `--model`, `--base-url`, `--permission-mode`, `--session`, `--working-dir`, `--mode`, `--mcp-servers`, `--tts-url`, `--stt-url`, `--search-url`, `--skills-dir`, `--assistant`, `--knowledge`, `--knowledge-dir`, `--knowledge-api-key`

## Subsystems

### Skills System

Skills are agent-manageable YAML+Python plugins discovered from three paths:
1. `--skills-dir` CLI flag (highest priority)
2. `<cwd>/skills/` (project-local skills)
3. `~/.redclaw/skills/` (user-global skills)

**Manifest formats:**
- `SKILL.md` — YAML frontmatter (`---` delimited) + markdown body with instructions
- `skill.yaml` — Pure YAML manifest

**Key classes:**
- `SkillBase` — Abstract base class for skill implementations
- `SkillManifest` — Parsed manifest data (name, description, tools, instructions)
- `SkillTool` — Tool definition exported by a skill

**Agent tools:** `skills_list`, `skill_view`, `skill_manage` (create/update/patch/delete)

### MCP Client

Model Context Protocol client using SSE transport:
- Persistent SSE connections for server-sent events
- JSON-RPC protocol for request/response
- Initialize handshake → endpoint discovery → tool discovery
- Tool registration uses `mcp__server__tool` naming convention
- Configured via `--mcp-servers` CLI flag

### Memory System

Persistent memory with frozen snapshot pattern:
- `MemoryManager` loads MEMORY.md + USER.md at session start → frozen snapshot injected into system prompt
- Live mutations via tool calls (`store`, `recall`, `search`) persist to disk immediately via atomic writes
- Snapshot never changes mid-session (preserves prefix cache)
- Format: markdown with `# Section` headers as categories, bullet entries
- Security: all content scanned for injection, exfiltration, and invisible unicode before storing
- Storage: `~/.redclaw/memory/`

### Subagent System

Isolated nested ConversationRuntime for delegated tasks:
- **Restricted toolset** — strips subagent, memory, skills tools to prevent recursion
- **Depth limiting** — max 2 levels of nesting
- **Turn limit** — default 5 tool rounds per subagent
- **Timeout** — default 60s per subagent
- **Batch mode** — up to 3 concurrent subtasks via semaphore
- **Subagent Types (Bloodlines)** — CODER (core+shell tools), SEARCHER (core+web tools), GENERAL (all non-excluded)
- **Retry-with-Reflection** — up to 3 retries with accumulated failure context; timeout escalates with each attempt
- **Wisdom Inheritance (Crypt)** — subagents inherit accumulated bloodline wisdom; results are entombed for future learning

### Toolsets

Named collections of tool names with recursive include resolution:

| Toolset | Tools |
|---------|-------|
| `core` | read_file, write_file, edit_file, glob_search, grep_search |
| `shell` | bash |
| `web` | web_search, web_reader |
| `memory` | memory |
| `skills` | skills_list, skill_view, skill_manage |
| `subagent` | subagent |
| `full` | includes core, shell, web |
| `readonly` | read_file, glob_search, grep_search |
| `assistant` | task, note, reminder |
| `knowledge` | knowledge |

Custom toolsets can be registered at runtime.

### Hooks

Pre/post tool shell hooks via `HookRunner`:
- `HookConfig` holds lists of shell commands for `pre_tool` and `post_tool` events
- Pre-hooks receive `REDCLAW_TOOL` and `REDCLAW_TOOL_INPUT` env vars; returning non-zero blocks the tool
- Post-hooks receive `REDCLAW_TOOL` and `REDCLAW_RESULT` env vars (result truncated to 4096 chars)
- 30s timeout per hook execution

### System Prompt Builder

`build_system_prompt()` assembles the system prompt from:
1. Base identity ("You are RedClaw...")
2. Working directory
3. Git context (branch, dirty/clean status)
4. CLAW.md instructions — discovered from working dir up to home dir (CLAW.md or .claw.md)
5. Extra instructions (e.g., subagent-specific guidance)
6. Memory snapshot (frozen at session start)
7. Skills guidance
8. Tool usage guidelines

### Channels

Abstract messaging layer in `redclaw/channels/`:
- `ChannelBase` — ABC with send_text, send_file, send_typing, start, stop
- `ChannelMessage` — normalized message (text, user_id, chat_id, file_path, raw)
- `ChannelConfig` — working_dir, allowed_users
- `TelegramChannel` — concrete implementation with message/file handlers, 4096 char splitting

### Content Security

`redclaw/tools/content_scan.py` provides three scanners:
- `scan_for_injection()` — detects prompt injection patterns
- `scan_for_exfiltration()` — detects data exfiltration attempts
- `scan_for_invisible_unicode()` — detects hidden unicode characters

Applied to memory stores and skill content.

### Local Servers

`servers/` directory contains MCP servers for local capabilities:
- `tts_server.py` — Text-to-speech with Coqui XTTS-v2 (voice cloning) or edge-tts fallback, FastMCP framework
- `stt_server.py` — Speech-to-text with Whisper base model, FastMCP framework
- `web_reader_server.py` — Web reader with Playwright headless browser + html2text, FastMCP framework
- `start_all.py` — Script to start all local MCP servers

### Assistant Subsystem

`redclaw/assistant/` — proactive personal assistant features for Telegram mode:
- **Config** — `AssistantConfig` dataclass with JSON persistence (`~/.redclaw/assistant/config.json`), supports `persona_name`, timezone, briefing preferences
- **Tasks** — `TaskStore` with add/list/update/delete/search (JSONL persistence in `~/.redclaw/assistant/tasks.jsonl`)
- **Notes** — `NoteStore` with CRUD + search (JSONL persistence in `~/.redclaw/assistant/notes.jsonl`)
- **Reminders** — `ReminderStore` with scheduling, pending queries, due-check (JSONL persistence)
- **Agent tools** — `task`, `note`, `reminder` registered via `redclaw/tools/assistant_tools.py`
- **Persona name** — configured via `persona_name` in config; prepended to assistant context so the LLM identifies by that name
- Enabled with `--assistant` CLI flag (Telegram mode only)

### Knowledge Graph

`redclaw/memory_graph/` — Cognee-backed persistent knowledge graph memory:
- **Tools** — `add` (store facts), `cognify` (process into graph), `search` (query the graph), `memify` (summarize to memory), `prune` (remove old entries)
- **Agent tool** — `knowledge` registered when `--knowledge` flag is set
- Storage: `~/.redclaw/knowledge/` (configurable via `--knowledge-dir`)
- Requires `cognee` optional dependency and a separate LLM API key (`--knowledge-api-key`)
- Enabled with `--knowledge` CLI flag

### Crypt (Wisdom Inheritance)

`redclaw/crypt/` — accumulates lessons from subagent runs for future wisdom:
- **Bloodlines** — per-type wisdom files (coder.md, searcher.md, general.md) with structured sections (Successful Patterns, Warnings, Tool Insights)
- **Dharma** — living document of cross-cutting patterns across all bloodlines
- **Entombed** — individual subagent records (JSON) with task, type, success, lessons, timestamp
- **Metrics** — aggregate counters (tasks_total, success, failure, by type)
- Storage: `~/.redclaw/crypt/`

## Conventions

- Python 3.11+ with `from __future__ import annotations` in most modules
- Dataclasses for all data types (no Pydantic)
- `logging` module throughout — no print statements in library code
- Tools return string results; errors surfaced via `is_error=True` in `ToolResultBlock`
- Atomic writes via tempfile + os.replace for all persistent file operations
- The Godot GUI project lives in `godot/` with GDScript in `godot/scripts/` and `godot/ui/`
