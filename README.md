<div align="center">
  <img src="assets/logo.png" alt="RedClaw" width="200">
  <h1>RedClaw</h1>
</div>

A minimal AI coding agent in two forms:

- **Python CLI** — headless REPL agent with streaming, tools, and session persistence
- **Godot 4.6 app** — IDE-like GUI that spawns the Python agent via JSON-RPC

Both share the same provider-agnostic LLM client, conversation loop, 6 tools, session persistence, and compaction.

## Dependencies

- Python 3.11+
- `httpx>=0.27` and `rich>=13` (only 2 deps)
- Any LLM provider (OpenAI, Anthropic, Ollama, Groq, DeepSeek, OpenRouter, or custom)
- [Godot 4.6](https://godotengine.org/) (for the GUI app)

## Install

```bash
cd redclaw
pip install -e .
```

## Usage

### CLI

```bash
# One-shot prompt
python -m redclaw --provider openai --model gpt-4o "list files in this project"

# Interactive REPL
python -m redclaw --provider openai --model gpt-4o

# With Ollama
python -m redclaw --provider ollama --model llama3 --base-url http://localhost:11434

# With custom API
python -m redclaw --provider openai --model glm-4.7 --base-url https://api.example.com/v4

# Read-only mode (no file writes or bash)
python -m redclaw --provider openai --permission-mode read_only
```

### CLI Flags

| Flag | Description |
|---|---|
| `--provider` | LLM provider: `openai`, `anthropic`, `ollama`, `groq`, `deepseek`, `openrouter` |
| `--model` | Model name (defaults per provider) |
| `--base-url` | Custom API base URL |
| `--permission-mode` | `ask`, `read_only`, `workspace_write`, `danger_full_access` |
| `--session` | Resume a session ID |
| `--working-dir` | Working directory (default: cwd) |
| `--mode` | `repl` (default) or `rpc` (for Godot) |

### Slash Commands (REPL)

| Command | Description |
|---|---|
| `/help` | Show available commands |
| `/compact` | Compact conversation history |
| `/clear` | Clear session history |
| `/usage` | Show token usage and cost |
| `/model` | Show current model |
| `/session` | Show session info |
| `/quit` | Exit |

### Godot App

1. Open `godot/` in Godot 4.6
2. Configure provider, model, and API key in the sidebar
3. Type messages and hit Send — the Python agent streams responses in real time

## Tools

| Tool | Permission | Description |
|---|---|---|
| `bash` | full access | Execute shell commands with timeout |
| `read_file` | read only | Read file contents (with line range) |
| `write_file` | workspace write | Write content to a file |
| `edit_file` | workspace write | Replace exact text in a file |
| `glob_search` | read only | Find files by glob pattern |
| `grep_search` | read only | Search file contents by regex |

## Architecture

```
redclaw/
  api/          Provider-agnostic LLM client, SSE parser, provider registry
  runtime/      Conversation loop, session, compaction, permissions, hooks
  tools/        6 core tool implementations
  cli.py        REPL with rich rendering
  rpc.py        JSON-RPC over stdio (Godot bridge)

godot/
  scripts/      Agent bridge, session manager, settings
  ui/           Chat panel, sidebar, tool panel, status bar, message items
```

## License

MIT
