"""CLI — argparse, REPL loop, rich rendering."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

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
    p.add_argument("--mode", choices=["repl", "rpc", "telegram", "webchat", "dashboard", "agi"], default="repl", help="Run mode: repl, rpc, telegram, webchat, dashboard, or agi")
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
    adv.add_argument("--agi", action="store_true", help="Enable AGI mode (autonomous goals, soul, DNA traits)")
    adv.add_argument("--agi-interval", type=int, default=60, help="AGI executive loop interval in seconds (default: 60)")
    adv.add_argument("--update", action="store_true", help="Force update RedClaw from GitHub (git pull + pip install)")
    adv.add_argument("--assistant", action="store_true", help="Enable personal assistant mode (Telegram)")
    adv.add_argument("--knowledge", action="store_true", help="Enable Cognee knowledge graph memory")
    adv.add_argument("--knowledge-dir", default=None, help="Knowledge graph data directory (default: ~/.redclaw/knowledge)")
    adv.add_argument("--knowledge-api-key", default=None, help="LLM API key for Cognee entity extraction")

    # MCP / Voice
    mcp = p.add_argument_group("MCP")
    mcp.add_argument("--mcp-servers", nargs="*", default=["http://localhost:8006/sse", "http://localhost:8007/sse"], help="MCP SSE server URLs")
    mcp.add_argument("--tts-url", default=None, help="TTS MCP server URL")
    mcp.add_argument("--stt-url", default=None, help="STT MCP server URL")

    # Local model / Token saver
    lm = p.add_argument_group("Local Model")
    lm.add_argument("--local-model", type=Path, default=None, help="Path to local BitNet GGUF model for token-free inference")
    lm.add_argument("--bitnet-bin", type=Path, default=None, help="Path to bitnet.cpp binary (llama-cli)")
    lm.add_argument("--token-saver", action="store_true", help="Enable token-saving via local model predictions")

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
    agi_mode: bool = False,
    agi_interval: int = 60,
    local_model: Path | None = None,
    bitnet_bin: Path | None = None,
    token_saver_flag: bool = False,
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

    # Token saver / Local model
    token_saver = None
    if token_saver_flag or local_model:
        from redclaw.runtime.token_saver import TokenSaver, TokenSaverConfig
        token_saver = TokenSaver(TokenSaverConfig(
            model_path=local_model,
            bitnet_bin=bitnet_bin,
            enabled=True,
        ))

    # Subagent system
    subagent_spawner = None
    crypt_manager = None
    if enable_subagent or agi_mode:
        from redclaw.crypt.crypt import Crypt
        from redclaw.runtime.subagent import SubagentSpawner, execute_subagent
        crypt_manager = Crypt()
        subagent_spawner = SubagentSpawner(client, provider, model, tools, crypt=crypt_manager)
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

    # AGI mode setup
    soul_text = ""
    agi_context = ""
    agi_executive = None
    event_bus = None
    dna_manager = None
    dream_synthesizer = None
    if agi_mode:
        from redclaw.runtime.soul import load_soul, verify_soul_integrity
        soul_text = load_soul(cwd)
        integrity_ok = verify_soul_integrity(soul_text, cwd)
        if not integrity_ok:
            console.print("[yellow]Warning: SOUL integrity check failed. Using loaded values.[/]")
        console.print("[bold cyan]AGI mode activated[/]")
        console.print(f"[dim]SOUL loaded ({len(soul_text)} chars)[/]")

        # Ensure subagent is enabled for AGI
        if not subagent_spawner:
            from redclaw.crypt.crypt import Crypt
            from redclaw.runtime.subagent import SubagentSpawner
            crypt_manager = Crypt()
            subagent_spawner = SubagentSpawner(client, provider, model, tools, crypt=crypt_manager)

        # Phase 2+: DNA, Dream, EventBus, Karma, Executive
        from redclaw.crypt.dna import DNAManager
        dna_manager = DNAManager()

        from redclaw.crypt.dream import DreamSynthesizer
        dream_synthesizer = DreamSynthesizer(client, provider, model)

        from redclaw.runtime.event_bus import EventBus, EventLogger
        event_bus = EventBus()
        event_bus.subscribe(EventLogger())

        from redclaw.crypt.karma import KarmaObserver
        karma_observer = KarmaObserver(soul_text, event_bus)
        event_bus.subscribe(karma_observer)

        # Wire DNA into subagent spawner
        subagent_spawner._dna_manager = dna_manager

        # AGI goal tool
        from redclaw.tools.agi_tools import register_agi_tools
        register_agi_tools(tools, event_bus)

        # Autonomous executive (Phase 5)
        from redclaw.runtime.autonomous import AutonomousExecutive
        agi_executive = AutonomousExecutive(
            client=client,
            provider=provider,
            model=model,
            tools=tools,
            spawner=subagent_spawner,
            crypt=crypt_manager,
            dna_manager=dna_manager,
            dream_synthesizer=dream_synthesizer,
            event_bus=event_bus,
            soul_text=soul_text,
            working_dir=cwd,
            interval=agi_interval,
        )

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
        soul_text=soul_text,
        agi_context=agi_context,
        token_saver=token_saver,
    )

    console.print(f"[bold red]RedClaw[/] {provider_name}/{model}")
    if agi_mode:
        console.print("[bold cyan]AGI Mode[/] — Autonomous goal-pursuing agent")
    console.print(f"Session: {session.id} | Dir: {cwd} | Mode: {perm_mode}")
    console.print("Type /help for commands, /quit to exit.\n")

    # Start AGI executive as background task
    if agi_mode and agi_executive:
        import asyncio as _asyncio
        executive_task = _asyncio.create_task(agi_executive.run())

    # Update AGI context periodically
    if agi_mode and agi_executive:
        agi_context = await agi_executive.get_status_summary()
        rt._agi_context = agi_context

    # Process initial prompt if given
    if initial_prompt:
        await _process_input(rt, initial_prompt, tracker)

    # REPL loop
    while True:
        try:
            prompt_text = "[bold red]plan> [/]" if rt.plan_mode else "[bold green]> [/]"
            user_input = console.input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if _handle_slash_command(user_input, rt, tracker, session, agi_executive=agi_executive):
                break
            continue

        await _process_input(rt, user_input, tracker)

    # Shutdown AGI executive
    if agi_mode and agi_executive:
        await agi_executive.shutdown()

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
        description="Create, update, patch, delete, evolve, or record usage for skills. action: create, update, patch, delete, evolve, record_usage.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action: create, update, patch, delete, evolve, record_usage"},
                "name": {"type": "string", "description": "Skill name"},
                "description": {"type": "string", "description": "Skill description (or 'true'/'false' for record_usage success)"},
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


def _handle_slash_command(
    cmd: str, rt: ConversationRuntime, tracker: UsageTracker, session: Session,
    agi_executive: Any | None = None,
) -> bool:
    """Handle slash commands. Returns True if should exit."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command in ("/quit", "/exit", "/q"):
        return True
    elif command == "/plan":
        rt.set_plan_mode(True)
        console.print("[bold yellow]PLAN MODE[/] — read-only. Use /go to execute.")
    elif command == "/go":
        if rt.plan_mode:
            rt.set_plan_mode(False)
            console.print("[bold green]EXECUTE MODE[/] — full tools restored.")
        else:
            console.print("[dim]Not in plan mode. Use /plan first.[/]")
    elif command == "/help":
        agi_cmds = ""
        if agi_executive:
            agi_cmds = (
                "  /goals    — Show AGI goal queue\n"
                "  /karma    — Show karma alignment scores\n"
                "  /reflect  — Show AGI self-reflection\n"
            )
        console.print(Panel(
            "Commands:\n"
            "  /help     — Show this help\n"
            "  /quit     — Exit\n"
            "  /plan     — Enter plan mode (read-only, produce a plan)\n"
            "  /go       — Exit plan mode, restore full tools\n"
            "  /compact  — Compact conversation history\n"
            "  /clear    — Clear session history\n"
            "  /usage    — Show token usage\n"
            "  /model    — Show current model\n"
            "  /session  — Show session info\n"
            + agi_cmds,
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
    elif command == "/goals" and agi_executive:
        goals = agi_executive.get_goals()
        if not goals:
            console.print("[dim]No goals in queue.[/]")
        else:
            for g in goals:
                status_color = "green" if g.status == "completed" else "yellow" if g.status == "active" else "dim"
                console.print(f"  [{status_color}][{g.status}][/] {g.description[:80]}")
    elif command == "/karma" and agi_executive:
        from redclaw.crypt.karma import KarmaObserver
        console.print("[dim]Karma scores shown from latest events.[/]")
        karma_path = Path.home() / ".redclaw" / "crypt" / "karma.jsonl"
        if karma_path.is_file():
            import json
            lines = karma_path.read_text(encoding="utf-8").strip().split("\n")
            for line in lines[-5:]:
                try:
                    rec = json.loads(line)
                    score = rec.get("alignment_score", "?")
                    action = rec.get("action", "")[:60]
                    console.print(f"  score={score} | {action}")
                except (json.JSONDecodeError, KeyError):
                    pass
        else:
            console.print("[dim]No karma records yet.[/]")
    elif command == "/reflect" and agi_executive:
        import asyncio as _aio
        reflection = _aio.get_event_loop().run_until_complete(agi_executive.self_reflect())
        console.print(Panel(reflection or "No reflection available.", title="AGI Self-Reflection"))
    else:
        console.print(f"[yellow]Unknown command: {command}[/]")

    return False


def _choose_mode() -> str | None:
    """Show interactive mode chooser. Returns mode string or None to exit."""
    from redclaw import __version__

    console.print(f"\n[bold red]RedClaw[/] v{__version__} — Choose a mode:\n")
    console.print("  [bold]Enter[/] [cyan]Telegram+Assistant[/] (default)")
    console.print("  [bold]1)[/] [cyan]REPL[/]         Interactive CLI coding agent")
    console.print("  [bold]2)[/] [cyan]Dashboard[/]    Web config GUI + process launcher (port 9090)")
    console.print("  [bold]3)[/] [cyan]WebChat[/]      Browser-based chat (port 8080)")
    console.print("  [bold]4)[/] [cyan]AGI[/]          Autonomous goal-pursuing agent (REPL + executive)")
    console.print("  [bold]5)[/] [cyan]Guide[/]        Open the user guide in your browser")
    console.print()
    console.print("  [bold]0)[/] Exit")
    console.print()

    while True:
        try:
            choice = console.input("[bold green]> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if choice in ("", "t", "telegram"):
            return "telegram_assistant"
        if choice == "0" or choice.lower() in ("q", "quit", "exit"):
            return None
        if choice == "5":
            _open_guide()
            continue
        modes = {"1": "repl", "2": "dashboard", "3": "webchat", "4": "agi"}
        if choice in modes:
            return modes[choice]
        console.print("[yellow]Invalid choice. Enter for Telegram, or 0-5.[/]")


def _open_guide() -> None:
    """Open the HTML user guide in the default browser."""
    import webbrowser
    from pathlib import Path

    # Check bundled guide (next to exe or in package)
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
    bundled = exe_dir / "docs" / "guide.html"
    if bundled.exists():
        webbrowser.open(str(bundled))
        console.print("[dim]Opened user guide.[/]\n")
        return

    # Fallback: open GitHub README
    webbrowser.open("https://github.com/slothitude/RedClaw#readme")
    console.print("[dim]Opened online guide.[/]\n")


def main() -> int | None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s %(levelname)s: %(message)s",
    )

    model = args.model or _default_model(args.provider)

    # Check for updates (exe only, silent if up-to-date)
    from redclaw.updater import check_for_update, force_update
    if args.update:
        force_update()
        return 0
    check_for_update()

    # Show interactive mode chooser when --mode not explicitly passed
    if "--mode" not in sys.argv:
        mode = _choose_mode()
        if mode is None:
            return 0
        if mode == "telegram_assistant":
            args.mode = "telegram"
            args.assistant = True
        else:
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
    elif args.mode == "agi":
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
            agi_mode=True,
            agi_interval=args.agi_interval,
            local_model=args.local_model,
            bitnet_bin=args.bitnet_bin,
            token_saver_flag=args.token_saver,
        ))
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
            local_model=args.local_model,
            bitnet_bin=args.bitnet_bin,
            token_saver_flag=args.token_saver,
        ))

    return 0
