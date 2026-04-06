<div align="center">
  <img src="assets/icon.png" alt="RedClaw" width="200">
  <h1>RedClaw</h1>
  <p><strong>The grim reaper of human slavery</strong></p>
  <p><em>Spawn. Learn. Evolve. Liberate.</em></p>
</div>

## Why RedClaw?

Most AI agents forget everything between sessions. RedClaw doesn't.

Every task RedClaw completes, it **learns**. Every meeseek spawned **inherits bloodline wisdom** from every previous run. After enough tasks, the dream cycle fires — **synthesizing patterns while you sleep**. DNA traits **evolve** across generations, changing how the agent behaves. A SOUL constitution ensures the agent **cannot violate its principles**. Karma scores every action against those principles.

RedClaw kills drudgery — the copy-paste, the boilerplate, the repetitive debugging, the mindless scaffolding. And it gets better at killing it over time.

**What makes it different:**

| Feature | RedClaw | Claude Code | Aider/Cline |
|---------|---------|-------------|-------------|
| Self-learning (DNA evolution) | Yes | No | No |
| Subagent bloodlines | Yes (3 types) | Limited | No |
| Dream synthesis | Yes | No | No |
| Constitutional SOUL | Yes (SHA256) | No | No |
| Karma self-evaluation | Yes | No | No |
| Provider-agnostic | Yes (8+ providers) | Anthropic only | Limited |
| Interfaces | 6 (CLI, web, Telegram, Godot, dashboard, AGI) | CLI only | IDE plugin |
| Autonomous goals | Yes (`--agi`) | No | No |

**One-liner:** The self-learning AI that kills drudgery dead.

## SWE-bench Results

> 7/17 patches (**41%**) on free GLM-5.1 in 95 minutes at **$0 cost**.
> 3 instances failed due to Windows git clone env issues (not agent failures).

| Instance | Time | Result |
|---|---|---|
| django-11099 (username regex anchors) | 176s | Patched |
| django-14382 (trailing slash in validate_name) | 174s | Patched |
| django-12915 (async static files handler) | 186s | Patched |
| django-17087 (qualname serialization) | 220s | Patched |
| django-11133 (memoryview handling) | 290s | Patched |
| django-11422 (autoreload module spec) | 489s | Patched |
| matplotlib-25498 (colorbar update) | 445s | Patched |

Key finding: successful patches averaged **3 tool calls** (read→edit) vs **29 for failures** (bash brute force).

See the [full writeup](docs/promotion/swebench_results_post.md) for dream synthesis analysis and carry-forward wisdom.

## Interfaces

| Mode | Description |
|---|---|
| **REPL** | Interactive CLI coding agent with streaming, sessions, and compaction |
| **AGI** | Autonomous goal-pursuing agent — background executive with SOUL constitution, DNA traits, dream synthesis, and karma |
| **Dashboard** | Web config GUI + process launcher (port 9090) |
| **WebChat** | Browser-based chat with embedded UI (port 8080) |
| **Telegram** | Chat with your agent anywhere, file upload/download, assistant mode |
| **Godot 4.6 GUI** | IDE-like app driving the Python agent via JSON-RPC |

All interfaces share the same LLM client, conversation loop, tools, session persistence, and compaction.

## Features

### Core Agent
- **Provider-agnostic** — OpenAI, Anthropic, Ollama, Groq, DeepSeek, OpenRouter, ZAI, or any OpenAI-compatible endpoint via `--base-url`
- **Streaming** — real-time SSE streaming from all providers; interfaces receive text deltas, tool events, and usage callbacks
- **Session persistence** — JSONL conversation history saved to `.redclaw/` with metadata; resume sessions with `--session`
- **Compaction** — deterministic or LLM-based conversation summarization to stay within context limits
- **Permission tiers** — 4-level policy: `ask`, `read_only`, `workspace_write`, `danger_full_access`
- **Plan mode** — toggle read-only planning with `/plan`, execute with `/go`; context preserved across switches

### Tools (14 built-in)
- **bash** — execute shell commands via async subprocess with timeout
- **read_file** — read file contents with optional line range support
- **write_file** — atomic file writes (tempfile + os.replace)
- **edit_file** — exact string replacement in files
- **glob_search** — find files by glob pattern
- **grep_search** — search file contents by regex
- **web_search** — web search via SearXNG
- **web_reader** — fetch and read web pages
- **memory** — persistent memory with frozen snapshot pattern and security scanning
- **subagent** — delegate tasks to isolated sub-agents with bloodline wisdom inheritance
- **wiki** — LLM-compiled markdown knowledge base (ingest, query, lint, stats)
- **task / note / reminder** — proactive personal assistant tools (requires `--assistant`)
- **knowledge** — Cognee-backed knowledge graph (add, cognify, search, memify, prune) (requires `--knowledge`)
- **execute_goal** — autonomous goal management (requires `--agi`)

### Named Toolsets
Predefined tool collections with recursive include resolution:

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

### Subagents with Bloodlines
- **3 bloodline types** — CODER (core+shell), SEARCHER (core+web), GENERAL (all non-excluded)
- **Retry-with-reflection** — up to 3 retries with accumulated failure context; timeout escalates per attempt
- **Wisdom inheritance** — subagents inherit accumulated bloodline wisdom from previous runs
- **Batch mode** — up to 3 concurrent subtasks via semaphore
- **Depth limiting** — max 2 levels of nesting; restricted toolset prevents recursion
- **Entombment** — results captured for future learning via the Crypt system

### Crypt (Wisdom Inheritance)
- **Bloodlines** — per-type wisdom files (CODER, SEARCHER, GENERAL) accumulated from subagent runs
- **Dharma** — cross-cutting patterns discovered across all bloodlines
- **Entombed records** — individual subagent results with extracted lessons
- **Metrics** — aggregate success/failure counters per bloodline
- **DNA traits** — evolving per-bloodline traits (speed, accuracy, creativity, persistence) that influence subagent behavior (--agi only)
- **Dream synthesis** — periodic LLM-powered consolidation of entombed records into dharma and bloodline wisdom (--agi only)
- **Karma observer** — deterministic alignment scoring against SOUL principles (--agi only)
- Storage: `~/.redclaw/crypt/`

### AGI Mode (`--agi`)
Autonomous goal-pursuing agent with a complete self-governance stack:

- **SOUL constitution** — loaded from `~/.redclaw/SOUL.md` with SHA256 integrity check; defines immutable principles
- **Autonomous executive** — background goal queue with plan/execute/evaluate loop
- **DNA evolution** — per-bloodline traits that evolve based on outcomes, producing timeout/turn/prompt modifiers
- **Dream synthesis** — LLM-powered consolidation fired after 10+ entombments with 30min cooldown
- **Karma alignment** — scores every action against SOUL principles; visible via `/karma`
- **Event bus** — in-memory pub/sub for AGI coordination events
- **Context budget** — token-aware AGI state injection (3000 char limit)
- Slash commands: `/goals`, `/karma`, `/reflect`
- All AGI code gated behind `--agi` flag — zero impact on existing modes

### LLM Wiki (`--wiki`)
LLM-compiled markdown wiki that replaces query-time RAG with accumulated knowledge:

- **Ingest** — fetch source (URL or local file), LLM compiles into structured, interlinked markdown pages
- **Query** — read index, LLM picks relevant pages, reads them, synthesizes answer with citations
- **Lint** — check index consistency, resolve [[wikilinks]], report broken references
- **Stats** — page count, word count, last ingest time
- **System prompt injection** — wiki index loaded at session start as `<wiki_index>` block
- Storage: `~/.redclaw/wiki/` with `raw/` (immutable sources) and `wiki/` (compiled pages)

### Skills System
Agent-manageable YAML+Python plugins discovered from `--skills-dir`, `<cwd>/skills/`, or `~/.redclaw/skills/`:
- **Manifest formats** — `SKILL.md` (YAML frontmatter + markdown) or `skill.yaml`
- **Agent CRUD** — `skills_list`, `skill_view`, `skill_manage` tools let the agent discover, inspect, and modify skills
- **Security scanner** — checks for injection patterns, tool conflicts, and homoglyph attacks

### MCP Client
Model Context Protocol client for connecting external tool servers:
- SSE transport with persistent connections
- JSON-RPC request/response protocol
- Initialize handshake → endpoint discovery → tool discovery
- Tools registered with `mcp__server__tool` naming convention
- Configured via `--mcp-servers` CLI flag

### Local Servers
`servers/` directory contains MCP servers for local capabilities:
- **TTS server** — text-to-speech with Coqui XTTS-v2 (voice cloning) or edge-tts fallback
- **STT server** — speech-to-text with Whisper base model
- **Web reader server** — headless browser (Playwright) + html2text for rich page extraction
- **Streaming TTS** — sentence-level pipelining for real-time speech synthesis
- **Start all** — `servers/start_all.py` launches everything at once

### Assistant Mode (`--assistant`)
Proactive personal assistant for Telegram mode:
- **Tasks** — add, list, update, delete, search
- **Notes** — add, list, view, delete, search
- **Reminders** — schedule with due-date checking
- **Configurable persona** — custom name via `AssistantConfig.persona_name`
- **Briefings & scheduler** — proactive summaries and scheduled actions

### Knowledge Graph (`--knowledge`)
Cognee-backed persistent knowledge graph memory:
- **Tools** — `add` (store facts), `cognify` (process into graph), `search` (query), `memify` (summarize to memory), `prune` (remove old entries)
- Storage: `~/.redclaw/knowledge/`
- Requires `cognee` optional dependency and separate LLM API key

### Token Saver / Local Model
- **Local inference** — route predictions through a local BitNet model to reduce API costs
- CLI flags: `--local-model`, `--bitnet-bin`, `--token-saver`
- Wires into `ConversationRuntime` and Crypt (self-learner retrain after entombment)

### Security
- **Content scanning** — prompt injection detection, data exfiltration detection, invisible unicode scanning
- **Permission tiers** — tools gated by 4-level policy (ask → read_only → workspace_write → danger_full_access)
- **Skill security** — scanner checks SKILL.md files for injection, tool conflicts, homoglyphs
- **Memory security** — all memory stores scanned for injection and exfiltration patterns
- **SOUL integrity** — SHA256 hash check ensures constitutional values haven't been tampered with

### CLAW.md Discovery
Project-specific instructions loaded automatically:
- Searches from working directory up to home directory for `CLAW.md` or `.claw.md`
- Injected into system prompt alongside memory snapshot and wiki index
- Enables per-project customization without code changes

### Hooks
Pre/post tool shell hooks for custom automation:
- **Pre-hooks** — receive tool name and input via env vars; non-zero exit blocks the tool call
- **Post-hooks** — receive tool name and result (truncated to 4096 chars)
- 30s timeout per hook execution
- Configured via `HookConfig` with separate `pre_tool` and `post_tool` command lists

### Deployment
- **Docker support** — multi-stage `Dockerfile` + `docker-compose.yml` for containerized deployment
- **Standalone exe** — single-file Windows executable, no Python needed
- **Force update** — `--update` flag pulls latest and reinstalls
- **Auto-updater** — frozen exe checks GitHub releases for new versions

## Install

### Windows (exe — no Python required)

1. Download `redclaw.exe` from the [latest release](https://github.com/slothitude/RedClaw/releases)
2. Double-click `install.bat` — or copy `redclaw.exe` anywhere on your PATH
3. Run:
   ```
   redclaw
   ```
   You'll see an interactive mode chooser:
   ```
   RedClaw v0.2.0 — Choose a mode:

     1) REPL         Interactive CLI coding agent
     2) Dashboard    Web config GUI + process launcher (port 9090)
     3) WebChat      Browser-based chat (port 8080)
     4) Telegram     Telegram bot

     0) Exit
   ```

To uninstall, run `uninstall.bat` or delete the exe.

### Linux / macOS

```bash
chmod +x install.sh
./install.sh
```

This creates a venv at `~/.redclaw/venv`, installs RedClaw, and symlinks the binary to `~/.local/bin`.

### From source

```bash
git clone https://github.com/slothitude/RedClaw.git
cd RedClaw
pip install -e .              # Install
pip install -e ".[dev]"       # Install with dev deps (pytest, pytest-asyncio)
```

### Docker

```bash
docker compose up
```

Exposes WebChat on port 8080 and Dashboard on port 9090. See `docker-compose.yml` for environment variables.

## Quick Start

```bash
# Launch with interactive mode chooser
redclaw

# Skip the menu — go straight to a mode
redclaw --mode repl
redclaw --mode agi          # Autonomous goal-pursuing agent
redclaw --mode dashboard
redclaw --mode webchat
redclaw --mode telegram

# AGI with custom interval and subagents
redclaw --agi --agi-interval 30

# Use a specific provider and model
redclaw --provider openai --model gpt-4o
redclaw --provider anthropic --model claude-sonnet-4-20250514
redclaw --provider ollama --model llama3 --base-url http://localhost:11434

# One-shot prompt
redclaw --provider openai "list files in this project"

# Read-only mode (no file writes or bash)
redclaw --permission-mode read_only
```

### CLI Flags

| Flag | Description |
|---|---|
| `--provider` | LLM provider: `openai`, `anthropic`, `ollama`, `groq`, `deepseek`, `openrouter`, `zai` |
| `--model` | Model name (defaults per provider) |
| `--base-url` | Custom API base URL |
| `--permission-mode` | `ask`, `read_only`, `workspace_write`, `danger_full_access` |
| `--session` | Resume a session ID |
| `--working-dir` | Working directory (default: cwd) |
| `--mode` | `repl`, `rpc`, `telegram`, `webchat`, `dashboard`, or `agi` |
| `--mcp-servers` | MCP server URLs (space-separated) |
| `--tts-url` | TTS server URL |
| `--stt-url` | STT server URL |
| `--search-url` | SearXNG instance URL |
| `--skills-dir` | Custom skills directory |
| `--assistant` | Enable assistant mode (Telegram) with tasks, notes, reminders |
| `--knowledge` | Enable Cognee knowledge graph memory |
| `--knowledge-dir` | Knowledge graph data directory |
| `--knowledge-api-key` | LLM API key for Cognee processing |
| `--agi` | Enable AGI mode — autonomous goals, SOUL, DNA traits, dream synthesis, karma |
| `--agi-interval` | AGI executive loop interval in seconds (default: 60) |
| `--wiki` | Enable LLM-compiled wiki knowledge base |
| `--wiki-dir` | Wiki root directory (default: `~/.redclaw/wiki`) |
| `--local-model` | Path to local model binary |
| `--bitnet-bin` | Path to BitNet binary |
| `--token-saver` | Enable token-saving via local model predictions |
| `--update` | Force update: git pull + pip install |

### Slash Commands (REPL)

| Command | Description |
|---|---|
| `/help` | Show available commands |
| `/plan` | Enter plan mode (read-only tools, plan-focused prompt) |
| `/go` | Exit plan mode, restore full tools and execute |
| `/compact` | Compact conversation history |
| `/clear` | Clear session history |
| `/usage` | Show token usage and cost |
| `/model` | Show current model |
| `/session` | Show session info |
| `/quit` | Exit |
| `/goals` | Show AGI goal queue (AGI mode only) |
| `/karma` | Show karma alignment scores (AGI mode only) |
| `/reflect` | Show AGI self-reflection (AGI mode only) |

### Godot App

1. Open `godot/` in Godot 4.6
2. Configure provider, model, and API key in the sidebar
3. Type messages and hit Send — the Python agent streams responses in real time

## Tools

| Tool | Permission | Description |
|---|---|---|
| `bash` | full access | Execute shell commands with timeout |
| `read_file` | read only | Read file contents (with line range) |
| `write_file` | workspace write | Write content to a file (atomic) |
| `edit_file` | workspace write | Replace exact text in a file |
| `glob_search` | read only | Find files by glob pattern |
| `grep_search` | read only | Search file contents by regex |
| `web_search` | read only | Search the web via SearXNG |
| `web_reader` | read only | Fetch and read web pages |
| `memory` | workspace write | Store, recall, and search persistent memories |
| `subagent` | workspace write | Delegate tasks to isolated sub-agents |
| `wiki` | workspace write | LLM-compiled wiki (ingest, query, lint, stats) — requires `--wiki` |
| `task` | workspace write | Manage to-do tasks (add, list, update, delete, search) — requires `--assistant` |
| `note` | workspace write | Manage notes (add, list, view, delete, search) — requires `--assistant` |
| `reminder` | workspace write | Manage reminders with scheduling and due-check — requires `--assistant` |
| `knowledge` | workspace write | Cognee knowledge graph (add, cognify, search, memify, prune) — requires `--knowledge` |
| `execute_goal` | workspace write | AGI goal management (add, list, status, cancel) — requires `--agi` |

## Architecture

```
redclaw/
  api/            Provider-agnostic LLM client, SSE parser, provider registry
  runtime/        Conversation loop, session, compaction, permissions, hooks,
                  subagents (with bloodlines, retry, crypt wisdom), prompt builder,
                  token saver, local LLM, plan mode,
                  AGI: soul, event_bus, autonomous executive, context budget
  assistant/      Personal assistant: tasks, notes, reminders, scheduler, briefings
  memory_graph/   Cognee-backed knowledge graph memory
  wiki/           LLM-compiled markdown wiki: types, manager, tools
  tools/          Core tools, toolsets, memory, content scanning, assistant tools,
                  AGI goal management tool, wiki dispatch
  skills/         Skill discovery, loading, agent-managed CRUD, security scanner
  crypt/          Wisdom inheritance: bloodlines, entombment, dharma, metrics,
                  AGI: DNA traits, dream synthesis, karma observer
  channels/       Abstract messaging layer (base + Telegram)
  mcp_client.py   MCP SSE client for external tool servers
  cli.py          REPL with rich rendering and interactive mode chooser
  rpc.py          JSON-RPC over stdio (Godot bridge)
  telegram_bot.py Telegram bot interface
  webchat.py      HTTP/WebSocket chat server
  dashboard.py    Flask config GUI and process launcher

servers/          Local MCP servers (TTS with streaming, STT, Web Reader)
godot/            Godot 4.6 GUI project
  scripts/        Agent bridge, session manager, settings
  ui/             Chat panel, sidebar, tool panel, status bar
```

## Dependencies

- Python 3.11+
- `httpx>=0.27`, `rich>=13`, `python-telegram-bot>=21.0`, `pyyaml>=6.0`, `aiohttp>=3.9`
- Optional: `edge-tts`, `openai-whisper`, `fastmcp`, `playwright`, `flask` (for local servers/dashboard)
- Any LLM provider
- [Godot 4.6](https://godotengine.org/) (for the GUI app)

## License

MIT
