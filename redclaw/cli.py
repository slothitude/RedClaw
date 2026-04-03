"""CLI — argparse, REPL loop, rich rendering."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

# Fix Windows cp1252 encoding for Unicode in console
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from redclaw.api.client import LLMClient
from redclaw.api.providers import get_provider
from redclaw.api.types import Usage
from redclaw.runtime.compact import CompactionConfig
from redclaw.runtime.conversation import ConversationCallbacks, ConversationRuntime, TurnSummary
from redclaw.runtime.hooks import HookRunner
from redclaw.runtime.permissions import PermissionMode, PermissionPolicy
from redclaw.runtime.session import Session, load_session, save_session, list_sessions
from redclaw.runtime.usage import UsageTracker
from redclaw.tools.registry import ToolExecutor

console = Console()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="redclaw",
        description="RedClaw — a minimal AI coding agent",
    )
    p.add_argument("prompt", nargs="?", help="Initial prompt (omit for interactive REPL)")
    p.add_argument("--provider", default="zai", help="LLM provider (zai, openai, anthropic, ollama, groq, deepseek, openrouter, or custom)")
    p.add_argument("--model", default="", help="Model name (provider default if omitted)")
    p.add_argument("--base-url", default=None, help="Custom API base URL")
    p.add_argument("--permission-mode", choices=["read_only", "workspace_write", "danger_full_access", "ask"], default="ask", help="Permission mode")
    p.add_argument("--session", default=None, help="Resume session ID")
    p.add_argument("--working-dir", default=None, help="Working directory (default: cwd)")
    p.add_argument("--mode", choices=["repl", "rpc", "telegram", "webchat", "dashboard"], default="repl", help="Run mode: repl, rpc, telegram, webchat, or dashboard")
    p.add_argument("--max-tokens", type=int, default=8192, help="Max output tokens")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")

    # Telegram options
    tg = p.add_argument_group("Telegram")
    tg.add_argument("--telegram-token", default=None, help="Telegram bot token (or set REDCLAW_TELEGRAM_TOKEN)")
    tg.add_argument("--telegram-user-id", type=int, default=None, help="Restrict to a single Telegram user ID (or set REDCLAW_TELEGRAM_USER_ID)")

    # WebChat options
    wc = p.add_argument_group("WebChat")
    wc.add_argument("--port", type=int, default=8080, help="WebChat port (default: 8080)")

    # Dashboard options
    db = p.add_argument_group("Dashboard")
    db.add_argument("--dashboard-port", type=int, default=9090, help="Dashboard port (default: 9090)")

    # Skills
    sk = p.add_argument_group("Skills")
    sk.add_argument("--skills-dir", nargs="*", default=None, help="Directories to search for skills")
    sk.add_argument("--skills-manage", action="store_true", help="Enable agent skill CRUD tools")

    # Memory
    mem = p.add_argument_group("Memory")
    mem.add_argument("--memory-dir", default=None, help="Memory directory (default: ~/.redclaw/memory)")

    # Advanced
    adv = p.add_argument_group("Advanced")
    adv.add_argument("--compact-llm", action="store_true", help="Enable LLM-based context compaction")
    adv.add_argument("--subagent", action="store_true", help="Enable subagent delegation")
    adv.add_argument("--assistant", action="store_true", help="Enable personal assistant mode (Telegram)")
    adv.add_argument("--knowledge", action="store_true", help="Enable Cognee knowledge graph memory")
    adv.add_argument("--knowledge-dir", default=None, help="Knowledge graph data directory (default: ~/.redclaw/knowledge)")
    adv.add_argument("--knowledge-api-key", default=None, help="LLM API key for Cognee entity extraction")

    # MCP / Voice
    mcp = p.add_argument_group("MCP")
    mcp.add_argument("--mcp-servers", nargs="*", default=["http://localhost:8006/sse", "http://localhost:8007/sse"], help="MCP SSE server URLs")
    mcp.add_argument("--tts-url", default=None, help="TTS MCP server URL")
    mcp.add_argument("--stt-url", default=None, help="STT MCP server URL")

    # Web search
    p.add_argument("--search-url", default="http://localhost:8888", help="SearXNG instance URL")
    p.add_argument("--reader-url", default="http://localhost:8003/sse", help="Web Reader API URL")

    return p


def _default_model(provider: str) -> str:
    defaults = {
        "openai": "gpt-4o",
        "anthropic": "claude-sonnet-4-20250514",
        "ollama": "llama3",
        "groq": "llama-3.3-70b-versatile",
        "deepseek": "deepseek-chat",
        "openrouter": "anthropic/claude-sonnet-4-20250514",
        "zai": "glm-5.1",
    }
    return defaults.get(provider, "gpt-4o")


async def _run_repl(
    provider_name: str,
    model: str,
    base_url: str | None,
    perm_mode: str,
    session_id: str | None,
    working_dir: str | None,
    initial_prompt: str | None,
    skills_dirs: list[str] | None = None,
    skills_manage: bool = False,
    search_url: str | None = None,
    reader_url: str | None = None,
    memory_dir: str | None = None,
    compact_llm: bool = False,
    enable_subagent: bool = False,
) -> None:
    """Run the interactive REPL."""
    cwd = working_dir or str(Path.cwd())
    provider = get_provider(provider_name, base_url)
    client = LLMClient(provider)

    # Load or create session
    if session_id:
        session = load_session(session_id, cwd)
        if session is None:
            console.print(f"[yellow]Session '{session_id}' not found. Creating new.[/]")
            session = Session(id=session_id or uuid.uuid4().hex[:8])
    else:
        session = Session(id=uuid.uuid4().hex[:8])

    session.model = model
    session.provider = provider_name
    session.working_dir = cwd

    tools = ToolExecutor(working_dir=cwd, search_url=search_url, reader_url=reader_url)
    policy = PermissionPolicy(mode=PermissionMode(perm_mode))
    tracker = UsageTracker()

    # Load skills
    if skills_dirs:
        _load_skills(skills_dirs, tools)

    # Skills CRUD tools
    memory_mgr = None
    if skills_manage:
        _register_skills_tools(tools)

    # Memory system
    if memory_dir is not None or skills_manage:
        from redclaw.tools.memory import MemoryManager, execute_memory
        memory_mgr = MemoryManager(memory_dir)
        from redclaw.api.types import PermissionLevel
        from redclaw.tools.registry import ToolSpec
        tools.register_tool(ToolSpec(
            name="memory",
            description="Persistent memory tool. Actions: recall, store, search. Store memories for cross-session persistence.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Operation: recall, store, search"},
                    "content": {"type": "string", "description": "Content to store (for 'store' action)"},
                    "category": {"type": "string", "description": "Category/section (default: General)", "default": "General"},
                    "query": {"type": "string", "description": "Search query (for recall/search)"},
                },
                "required": ["action"],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=lambda **kw: execute_memory(memory_dir=memory_dir, **kw),
        ))

    # Subagent system
    subagent_spawner = None
    if enable_subagent:
        from redclaw.runtime.subagent import SubagentSpawner, execute_subagent
        subagent_spawner = SubagentSpawner(client, provider, model, tools)
        from redclaw.api.types import PermissionLevel
        from redclaw.tools.registry import ToolSpec
        tools.register_tool(ToolSpec(
            name="subagent",
            description="Delegate a task to an isolated sub-agent. Provide a single task or newline-separated tasks for batch.",
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Single task description"},
                    "tasks": {"type": "string", "description": "Newline-separated tasks for batch execution"},
                },
                "required": ["task"],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=lambda **kw: execute_subagent(spawner=subagent_spawner, **kw),
        ))

    rt = ConversationRuntime(
        client=client,
        provider=provider,
        model=model,
        session=session,
        tools=tools,
        permission_policy=policy,
        usage_tracker=tracker,
        working_dir=cwd,
        memory=memory_mgr,
        subagent_spawner=subagent_spawner,
    )

    console.print(f"[bold red]RedClaw[/] {provider_name}/{model}")
    console.print(f"Session: {session.id} | Dir: {cwd} | Mode: {perm_mode}")
    console.print("Type /help for commands, /quit to exit.\n")

    # Process initial prompt if given
    if initial_prompt:
        await _process_input(rt, initial_prompt, tracker)

    # REPL loop
    while True:
        try:
            user_input = console.input("[bold green]> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if _handle_slash_command(user_input, rt, tracker, session):
                break
            continue

        await _process_input(rt, user_input, tracker)

    await client.close()


def _load_skills(skills_dirs: list[str], tools: ToolExecutor) -> None:
    """Load skills from directories and register their tools."""
    try:
        from redclaw.skills.loader import discover_skills, register_skill_tools
        skills = discover_skills(skills_dirs)
        if skills:
            register_skill_tools(skills, tools)
            console.print(f"[dim]Loaded {len(skills)} skill(s)[/]")
    except ImportError:
        console.print("[yellow]PyYAML not installed. Skills unavailable.[/]")


def _register_skills_tools(tools: ToolExecutor) -> None:
    """Register agent-facing skill CRUD tools."""
    from redclaw.api.types import PermissionLevel
    from redclaw.tools.registry import ToolSpec
    from redclaw.skills.agent_tools import execute_skills_list, execute_skill_view, execute_skill_manage

    tools.register_tool(ToolSpec(
        name="skills_list",
        description="List all discovered skills with metadata.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        permission=PermissionLevel.READ_ONLY,
        execute=lambda **kw: execute_skills_list(**kw),
    ))
    tools.register_tool(ToolSpec(
        name="skill_view",
        description="View skill content. detail: metadata, full, or references.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name"},
                "detail": {"type": "string", "description": "Detail level: metadata, full, references", "default": "metadata"},
            },
            "required": ["name"],
        },
        permission=PermissionLevel.READ_ONLY,
        execute=lambda **kw: execute_skill_view(**kw),
    ))
    tools.register_tool(ToolSpec(
        name="skill_manage",
        description="Create, update, patch, or delete skills. action: create, update, patch, delete.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action: create, update, patch, delete"},
                "name": {"type": "string", "description": "Skill name"},
                "description": {"type": "string", "description": "Skill description"},
                "instructions": {"type": "string", "description": "Skill instructions (markdown body)"},
                "version": {"type": "string", "description": "Version string", "default": "1.0"},
            },
            "required": ["action", "name"],
        },
        permission=PermissionLevel.WORKSPACE_WRITE,
        execute=lambda **kw: execute_skill_manage(**kw),
    ))


async def _process_input(rt: ConversationRuntime, user_input: str, tracker: UsageTracker) -> None:
    """Process a single user input through the runtime."""
    collected_text = ""
    current_tool = ""

    async def on_text_delta(text: str) -> None:
        nonlocal collected_text
        collected_text += text

    async def on_tool_begin(tool_id: str, name: str, input_json: str) -> None:
        nonlocal current_tool
        current_tool = name
        console.print(f"\n  [dim]▶ {name}[/]")

    async def on_tool_result(tool_id: str, result: str, is_error: bool) -> None:
        color = "red" if is_error else "dim"
        lines = result.split("\n")
        preview = "\n".join(lines[:5])
        if len(lines) > 5:
            preview += f"\n  ... ({len(lines)} lines total)"
        console.print(f"  [{color}]{preview}[/]")

    async def on_usage(usage: Usage) -> None:
        pass  # Updated at end of turn

    async def on_error(msg: str) -> None:
        console.print(f"\n[red]Error: {msg}[/]")

    cb = ConversationCallbacks(
        on_text_delta=on_text_delta,
        on_tool_begin=on_tool_begin,
        on_tool_result=on_tool_result,
        on_usage=on_usage,
        on_error=on_error,
    )

    with Live(console=console, refresh_per_second=8, vertical_overflow="visible") as live:
        async def _on_text(text: str) -> None:
            nonlocal collected_text
            collected_text += text
            live.update(Markdown(collected_text))

        cb.on_text_delta = _on_text
        summary = await rt.run_turn(user_input, cb)

    # Print final output
    if summary.error:
        console.print(f"[red]Turn error: {summary.error}[/]")
    console.print(f"[dim]{tracker.summary()}[/]\n")


def _handle_slash_command(cmd: str, rt: ConversationRuntime, tracker: UsageTracker, session: Session) -> bool:
    """Handle slash commands. Returns True if should exit."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command in ("/quit", "/exit", "/q"):
        return True
    elif command == "/help":
        console.print(Panel(
            "Commands:\n"
            "  /help     — Show this help\n"
            "  /quit     — Exit\n"
            "  /compact  — Compact conversation history\n"
            "  /clear    — Clear session history\n"
            "  /usage    — Show token usage\n"
            "  /model    — Show current model\n"
            "  /session  — Show session info",
            title="RedClaw Help",
        ))
    elif command == "/compact":
        compact_session(rt.session)
        console.print("[dim]Session compacted.[/]")
    elif command == "/clear":
        rt.session.messages.clear()
        console.print("[dim]Session cleared.[/]")
    elif command == "/usage":
        console.print(tracker.summary())
    elif command == "/model":
        console.print(f"Model: {rt.model}")
    elif command == "/session":
        console.print(f"Session: {session.id} | Messages: {len(session.messages)} | Dir: {session.working_dir}")
    else:
        console.print(f"[yellow]Unknown command: {command}[/]")

    return False


def _choose_mode() -> str | None:
    """Show interactive mode chooser. Returns mode string or None to exit."""
    from redclaw import __version__

    console.print(f"\n[bold red]RedClaw[/] v{__version__} — Choose a mode:\n")
    console.print("  [bold]1)[/] [cyan]REPL[/]         Interactive CLI coding agent")
    console.print("  [bold]2)[/] [cyan]Dashboard[/]    Web config GUI + process launcher (port 9090)")
    console.print("  [bold]3)[/] [cyan]WebChat[/]      Browser-based chat (port 8080)")
    console.print("  [bold]4)[/] [cyan]Telegram[/]     Telegram bot")
    console.print()
    console.print("  [bold]0)[/] Exit")
    console.print()

    while True:
        try:
            choice = console.input("[bold green]> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        modes = {"1": "repl", "2": "dashboard", "3": "webchat", "4": "telegram"}
        if choice == "0" or choice.lower() in ("q", "quit", "exit"):
            return None
        if choice in modes:
            return modes[choice]
        console.print("[yellow]Invalid choice. Enter 0-4.[/]")


def main() -> int | None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s %(levelname)s: %(message)s",
    )

    model = args.model or _default_model(args.provider)

    # Show interactive mode chooser when --mode not explicitly passed
    if "--mode" not in sys.argv:
        mode = _choose_mode()
        if mode is None:
            return 0
        args.mode = mode

    if args.mode == "rpc":
        from redclaw.rpc import run_rpc
        asyncio.run(run_rpc(
            provider_name=args.provider,
            model=model,
            base_url=args.base_url,
            perm_mode=args.permission_mode,
            session_id=args.session,
            working_dir=args.working_dir,
        ))
    elif args.mode == "telegram":
        from redclaw.telegram_bot import RedClawTelegramBot
        token = args.telegram_token or os.environ.get("REDCLAW_TELEGRAM_TOKEN", "")
        user_id = args.telegram_user_id or int(os.environ.get("REDCLAW_TELEGRAM_USER_ID", "0")) or None
        if not token:
            console.print("[red]Error: --telegram-token or REDCLAW_TELEGRAM_TOKEN required[/]")
            return 1
        bot = RedClawTelegramBot(
            token=token,
            allowed_user_id=user_id,
            working_dir=args.working_dir,
            provider_name=args.provider,
            model=model,
            base_url=args.base_url,
            perm_mode=args.permission_mode,
            search_url=args.search_url,
            reader_url=args.reader_url,
            mcp_servers=args.mcp_servers,
            assistant_mode=args.assistant,
        )
        asyncio.run(bot.run())
    elif args.mode == "webchat":
        from redclaw.webchat import run_webchat
        asyncio.run(run_webchat(
            provider_name=args.provider,
            model=model,
            base_url=args.base_url,
            perm_mode=args.permission_mode,
            working_dir=args.working_dir,
            port=args.port,
        ))
    elif args.mode == "dashboard":
        from redclaw.dashboard import run_dashboard
        run_dashboard(port=args.dashboard_port)
    else:
        asyncio.run(_run_repl(
            provider_name=args.provider,
            model=model,
            base_url=args.base_url,
            perm_mode=args.permission_mode,
            session_id=args.session,
            working_dir=args.working_dir,
            initial_prompt=args.prompt,
            skills_dirs=args.skills_dir,
            skills_manage=args.skills_manage,
            search_url=args.search_url,
            reader_url=args.reader_url,
            memory_dir=args.memory_dir,
            compact_llm=args.compact_llm,
            enable_subagent=args.subagent,
        ))

    return 0
