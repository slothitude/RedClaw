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
    p.add_argument("--mode", choices=["repl", "rpc", "telegram", "webchat"], default="repl", help="Run mode: repl, rpc, telegram, or webchat")
    p.add_argument("--max-tokens", type=int, default=8192, help="Max output tokens")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")

    # Telegram options
    tg = p.add_argument_group("Telegram")
    tg.add_argument("--telegram-token", default=None, help="Telegram bot token (or set REDCLAW_TELEGRAM_TOKEN)")
    tg.add_argument("--telegram-user-id", type=int, default=None, help="Restrict to a single Telegram user ID (or set REDCLAW_TELEGRAM_USER_ID)")

    # WebChat options
    wc = p.add_argument_group("WebChat")
    wc.add_argument("--port", type=int, default=8080, help="WebChat port (default: 8080)")

    # Skills
    sk = p.add_argument_group("Skills")
    sk.add_argument("--skills-dir", nargs="*", default=None, help="Directories to search for skills")

    # MCP / Voice
    mcp = p.add_argument_group("MCP")
    mcp.add_argument("--mcp-servers", nargs="*", default=None, help="MCP SSE server URLs")
    mcp.add_argument("--tts-url", default=None, help="TTS MCP server URL")
    mcp.add_argument("--stt-url", default=None, help="STT MCP server URL")

    # Web search
    p.add_argument("--search-url", default=None, help="SearXNG instance URL (e.g. http://100.84.161.63:8080)")
    p.add_argument("--reader-url", default=None, help="Web Reader API URL (e.g. http://100.84.161.63:8003)")

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
    search_url: str | None = None,
    reader_url: str | None = None,
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

    rt = ConversationRuntime(
        client=client,
        provider=provider,
        model=model,
        session=session,
        tools=tools,
        permission_policy=policy,
        usage_tracker=tracker,
        working_dir=cwd,
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


def main() -> int | None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s %(levelname)s: %(message)s",
    )

    model = args.model or _default_model(args.provider)

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
            search_url=args.search_url,
            reader_url=args.reader_url,
        ))

    return 0
