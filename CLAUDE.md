# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RedClaw is a minimal AI coding agent with multiple interfaces (CLI REPL, Godot 4.6 GUI via JSON-RPC, Telegram bot, WebChat, Dashboard). It's provider-agnostic — supports OpenAI, Anthropic, Ollama, Groq, DeepSeek, OpenRouter, ZAI, and custom LLM providers through a unified adapter layer.

## Commands

```bash
pip install -e .              # Install
pip install -e ".[dev]"       # Install with dev deps (pytest, pytest-asyncio)
python -m redclaw             # Run CLI REPL
pytest                        # Run tests (no test suite yet)
pytest tests/test_foo.py      # Run a single test file
pytest -x                     # Run tests, stop on first failure
```

No linter or formatter is configured. CI runs `pytest --tb=short` on Python 3.11 via GitHub Actions (`.github/workflows/ci.yml`).

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
    prompt.py       → system prompt builder: CLAW.md discovery, git context, memory snapshot, AGI context
    hooks.py        → pre/post tool shell hooks (HookRunner, HookConfig)
    subagent.py     → isolated nested ConversationRuntime for subtasks (DNA-aware)
    subagent_types.py → SubagentType enum (CODER, SEARCHER, GENERAL) with typed prompts/toolsets
    usage.py        → token usage tracking with cost estimation
    soul.py         → SOUL.md constitutional values with SHA256 integrity check (--agi only)
    event_bus.py    → in-memory pub/sub for AGI coordination (--agi only)
    autonomous.py   → background goal-pursuing executive (--agi only)
    context_budget.py → token-aware AGI state injection (--agi only)
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
    crypt.py     → Crypt manager: bloodline wisdom, entombment, dharma, DNA evolution, dream trigger
    extractor.py → lesson extraction from subagent results
    metrics.py   → CryptMetrics aggregate tracking + persistence
    dna.py       → DNA trait evolution per bloodline (--agi only)
    dream.py     → Brahman Dream synthesis (--agi only)
    karma.py     → Karma alignment observer (--agi only)
    ↓
MCP Client (redclaw/mcp_client.py)
    SSE-based protocol, persistent connections, JSON-RPC, tool discovery
    Tool registration with mcp__server__tool prefix convention
    ↓
Wiki (redclaw/wiki/)
    types.py   → WikiPage, IngestRecord, WikiStats dataclasses
    manager.py → WikiManager: ingest (LLM compile), query (index-first), lint, stats
    tools.py   → execute_wiki dispatch + singleton get_wiki_manager()
    ↓
Simulation (redclaw/sim/)
    types.py   → SimEntity, SimParameter, SimMetrics dataclasses
    engine.py  → SimEngine: pure-math 2D physics (Euler integration, damping, boundary bounce)
    runner.py  → SimRunner: async tick loop wrapper (~30fps) with emit callbacks
    tools.py   → Tool registration: spawn_entity, set_sim_parameter, query_state, apply_force
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

Run `python -m redclaw --help` for full flag list. Version is maintained in both `redclaw/__init__.py` (`__version__`) and `pyproject.toml` (`version`) — keep in sync.

Key simulation flag: `--sim` enables the simulation controller (2D physics world with entity spawning via agent tools). Available in REPL and RPC modes.

### Plan Mode

Toggleable read-only planning in the REPL via `ConversationRuntime.set_plan_mode()`:
- `/plan` — restricts tools to readonly set (`read_file`, `glob_search`, `grep_search`), appends plan instructions to system prompt, prompt changes to red `plan>`
- `/go` — restores full tools and original system prompt, prompt returns to green `>`
- Context from plan mode is preserved when switching back to execute mode
- Storage: in-memory state on `ConversationRuntime` (`_plan_mode`, `_original_tools`, `_original_system_prompt`)

### Force Update

`--update` flag triggers `force_update()` in `redclaw/updater.py`:
- Finds repo root by walking up from package directory looking for `.git/`
- Runs `git pull` in the repo root
- Runs `pip install -e .` to reinstall
- Shows version before/after
- Works for source/pip installs; frozen exe uses the existing `_do_update()` path (downloads new exe from GitHub release)

## Subsystems

### Skills System

Agent-manageable YAML+Python plugins discovered from `--skills-dir`, `<cwd>/skills/`, or `~/.redclaw/skills/`. Manifest formats: `SKILL.md` (YAML frontmatter + markdown) or `skill.yaml`. Key classes: `SkillBase`, `SkillManifest`, `SkillTool`. Agent tools: `skills_list`, `skill_view`, `skill_manage`.

### MCP Client

Model Context Protocol client using SSE transport:
- Persistent SSE connections for server-sent events
- JSON-RPC protocol for request/response
- Initialize handshake → endpoint discovery → tool discovery
- Tool registration uses `mcp__server__tool` naming convention
- Configured via `--mcp-servers` CLI flag

### Memory System

Persistent memory with frozen snapshot pattern: `MemoryManager` loads MEMORY.md + USER.md at session start, snapshot injected into system prompt. Live mutations persist immediately but snapshot stays frozen (preserves prefix cache). Storage: `~/.redclaw/memory/`.

### Subagent System

Isolated nested ConversationRuntime for delegated tasks:
- **Restricted toolset** — strips subagent, memory, skills tools to prevent recursion
- **Depth limiting** — max 2 levels of nesting
- **Turn limit** — default 5 tool rounds per subagent
- **Timeout** — default 60s per subagent
- **Batch mode** — up to 3 concurrent subtasks via semaphore
- **Subagent Types (Bloodlines)** — CODER (core+shell tools), SEARCHER (core+web tools), GENERAL (all non-excluded), SIMULATOR (core+simulator tools)
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
| `agi` | execute_goal |
| `wiki` | wiki |
| `simulator` | spawn_entity, set_sim_parameter, query_state, apply_force |
| `simulator` | spawn_entity, set_sim_parameter, query_state, apply_force |

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
7. Wiki index (injected as `<wiki_index>` block when `--wiki` enabled)
8. Skills guidance
9. Tool usage guidelines

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

`redclaw/assistant/` — proactive personal assistant for Telegram mode: tasks, notes, reminders, config with persona name. Agent tools: `task`, `note`, `reminder`. Enabled with `--assistant` CLI flag.

### Knowledge Graph

`redclaw/memory_graph/` — Cognee-backed persistent knowledge graph memory:
- **Tools** — `add` (store facts), `cognify` (process into graph), `search` (query the graph), `memify` (summarize to memory), `prune` (remove old entries)
- **Agent tool** — `knowledge` registered when `--knowledge` flag is set
- Storage: `~/.redclaw/knowledge/` (configurable via `--knowledge-dir`)
- Requires `cognee` optional dependency and a separate LLM API key (`--knowledge-api-key`)
- Enabled with `--knowledge` CLI flag

### Crypt (Wisdom Inheritance)

`redclaw/crypt/` — accumulates lessons from subagent runs for future wisdom:
- **Bloodlines** — per-type wisdom files (CODER, SEARCHER, GENERAL)
- **Dharma** — cross-cutting patterns across all bloodlines
- **Entombed** — individual subagent records with lessons
- **Metrics** — aggregate success/failure counters
- **DNA Traits** — evolving per-bloodline traits that influence subagent behavior
- **Dream Synthesis** — periodic LLM-powered consolidation of entombed records
- **Karma Observer** — deterministic alignment scoring against SOUL principles
- Storage: `~/.redclaw/crypt/`

### LLM Wiki (Phase 1 Active)

LLM-compiled markdown wiki that replaces query-time RAG with accumulated knowledge. The LLM **compiles** raw sources into structured, interlinked markdown pages, then **answers questions from the wiki** — knowledge persists across queries instead of being rediscovered each time.

**Module:** `redclaw/wiki/`
- `types.py` — `WikiPage`, `IngestRecord`, `WikiStats` dataclasses
- `manager.py` — `WikiManager`: ingest (LLM compile), query (index-first), lint, stats
- `tools.py` — `execute_wiki` dispatch function + `get_wiki_manager()` singleton

**Storage:** `~/.redclaw/wiki/` (configurable via `--wiki-dir`)
- `raw/<topic>/<slug>.md` — immutable source material
- `wiki/<topic>/<slug>.md` — LLM-compiled wiki pages
- `wiki/index.md` — content-oriented page listing
- `log.md` — append-only operation log

**CLI flags:** `--wiki` (enable), `--wiki-dir` (custom path)

**Agent tool:** `wiki` (single tool with action dispatch, mirrors `memory` pattern)
- `wiki ingest` — fetch source (URL via httpx or local file), LLM compile into structured page, update index
- `wiki query` — read index, LLM picks relevant pages, reads them, LLM synthesizes answer with citations
- `wiki lint` — check index consistency, resolve [[wikilinks]], report broken references
- `wiki stats` — page count, word count, last ingest time

**System prompt injection:** Wiki index loaded at session start and injected as `<wiki_index>` block, giving the LLM awareness of available wiki content.

**Design decisions:**
- Plain markdown over Cognee — auditable, portable, no embeddings, no vector DB
- Single `wiki` tool with action dispatch (same pattern as `memory`) rather than separate tools
- Index-first query — LLM reads index (~5K tokens), picks relevant pages, synthesizes with citations
- Atomic writes (tempfile + os.replace) for all persistent file operations
- `WikiManager` receives LLM client for compile/query (same pattern as `DreamSynthesizer`)

**Phase 2 (planned):** subagent workers for parallel ingest, auto-lint, WIKI.md schema discovery
**Phase 3 (planned):** skill packaging, auto-ingest, cross-wiki backlinks

Full spec: `docs/llm-wiki-spec.md`

### Simulation Controller

2D math+physics simulation world where the AI spawns and evolves entities. Gated behind `--sim` CLI flag.

**Module:** `redclaw/sim/`
- `types.py` — `SimEntity`, `SimParameter`, `SimMetrics` dataclasses
- `engine.py` — `SimEngine`: pure-math 2D physics (Euler integration, velocity damping, boundary bounce, gravity, force impulses)
- `runner.py` — `SimRunner`: async tick loop (~30fps) with emit callbacks for downstream consumers
- `tools.py` — `register_sim_tools()` registers 4 `ToolSpec`s with `ToolExecutor`

**Agent tools:** `spawn_entity`, `set_sim_parameter`, `query_state`, `apply_force` (registered via `simulator` toolset)
**CLI flag:** `--sim` (enable, wires into REPL and RPC modes)
**RPC method:** `sim_command` with actions: spawn_entity, remove_entity, set_parameter, query_state, apply_force, start, stop, reset, get_metrics

**Engine parameters (tunable):** gravity (default 0), damping (0.98), bounds_restitution (0.8), tick_rate (30fps)
**World bounds:** [-500, -500] to [500, 500]
**Entity types:** particle (small, fast), orb (large, gradient), field (transparent rect), constraint (line to nearest)
**Stability score:** 0-1 based on average velocity magnitude (lower = more stable)

**Godot rendering:**
- `godot/scripts/sim_controller.gd` — Node2D entity renderer with type-specific visuals, position lerping, camera pan/zoom
- `godot/ui/sim_panel.gd` — SubViewport + overlay controls (play/pause, reset, speed slider, metrics labels)
- Layout: simulation panel center, chat in collapsible left sidebar, right tabs unchanged

**SIMULATOR bloodline:**
- `SubagentType.SIMULATOR` — system prompt focused on spawning/tuning/stabilizing entities
- Toolset: `["core", "simulator"]` — file ops + sim tools
- DNA profile: speed=0.4, accuracy=0.7, creativity=0.8, persistence=0.6 (defaults to "creative" prompt style)
- Dream synthesis includes `=== SIMULATOR ===` section for accumulated wisdom

**AGI integration:**
- Event bus: `EVENT_SIM_CREATED`, `EVENT_SIM_TICK_MILESTONE`, `EVENT_SIM_STABILITY_CHANGED`
- Karma: positive keywords (stable, balanced, coherent, orbital, equilibrium)
- Context budget: `sim_state` section (200 char budget)
- Autonomous executive: accepts `"simulator"` as valid subagent type for goal decomposition

**CLAW.md directives:** Project-root `CLAW.md` auto-discovered by `prompt.py`, provides geometry rules, physics constraints, entity specs, stability targets.

**Tests:** `tests/test_sim.py` — 41 tests covering engine, runner, tools, bloodline, DNA, context budget, events, karma, RPC handler.

### AGI Executive (Active Autonomous Mode)

Activated via `--agi` CLI flag or mode chooser option 5. All AGI code is gated behind this flag — existing modes are completely unaffected.

```
                    ┌──────────────────────────────┐
                    │      AGI Executive            │
                    │  (autonomous.py)              │
                    │  - Goal queue (background)    │
                    │  - Plan/execute/evaluate loop │
                    └──────────┬───────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐ ┌──────▼───────┐ ┌──────▼──────────┐
    │  Event Bus     │ │   SOUL.md    │ │  Karma Observer  │
    │  (event_bus.py)│ │  (soul.py)   │ │  (karma.py)      │
    └────────────────┘ └──────────────┘ └──────────────────┘
              │                                 │
    ┌─────────▼──────────────────────────────────▼──────────┐
    │                  Crypt (extended)                       │
    │  - dream.py   : Brahman Dream synthesis                │
    │  - dna.py     : Trait evolution per bloodline          │
    │  - crypt.py   : existing (entombment, wisdom, dharma)  │
    └────────────────────────────────────────────────────────┘
              │
    ┌─────────▼──────────────────────────────────────────────┐
    │           Existing Runtime Layer (unchanged)            │
    └────────────────────────────────────────────────────────┘
```

**Components:**

- **SOUL.md** (`soul.py`) — Constitutional value system loaded from `~/.redclaw/SOUL.md`, SHA256 integrity check
- **DNA Traits** (`dna.py`) — Per-bloodline evolving traits that produce timeout/turn/prompt modifiers
- **Dream Synthesis** (`dream.py`) — Periodic LLM-powered consolidation of entombed records into dharma and bloodline wisdom (includes SIMULATOR bloodline)
- **Event Bus** (`event_bus.py`) — In-memory pub/sub for AGI coordination events
- **Karma Observer** (`karma.py`) — Deterministic alignment scoring against SOUL principles
- **Autonomous Executive** (`autonomous.py`) — Background goal queue with plan/execute/evaluate loop
- **AGI Tools** (`agi_tools.py`) — `execute_goal` tool (add, list, status, cancel goals)
- **Context Budget** (`context_budget.py`) — Token-aware AGI state injection (3200 char budget, includes sim_state)

**CLI flags:** `--agi` (enable), `--agi-interval` (loop interval, default 60s)
**Slash commands (AGI mode only):** `/goals`, `/karma`, `/reflect`
**Storage:** `~/.redclaw/agi/`, `~/.redclaw/crypt/dna/`, `~/.redclaw/SOUL.md`

**Safety:** All AGI code gated behind `--agi` — no impact on existing modes. Autonomous executive respects the same PermissionPolicy as the main agent. Goal decomposition and dream synthesis are token-capped; failed goals parked not retried.

## Conventions

- Python 3.11+ with `from __future__ import annotations` in most modules
- Dataclasses for all data types (no Pydantic)
- `logging` module throughout — no print statements in library code
- Tools return string results; errors surfaced via `is_error=True` in `ToolResultBlock`
- Atomic writes via tempfile + os.replace for all persistent file operations
- Version is maintained in both `redclaw/__init__.py` (`__version__`) and `pyproject.toml` (`version`) — keep in sync
- The Godot GUI project lives in `godot/` with GDScript in `godot/scripts/` and `godot/ui/`
- Docker: multi-stage `Dockerfile` + `docker-compose.yml` for containerized deployment

### Common Pitfalls

- **Dashboard checkboxes** — must appear in both `getConfig()` and `setConfig()` JS functions, or they're silently dropped on save
- **System prompt identity** — `prompt.py` base identity ("You are RedClaw") overrides anything added later in `assistant_context`; persona name is injected separately
- **Version sync** — bump both `redclaw/__init__.__version__` and `pyproject.toml version` together
