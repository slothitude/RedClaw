"""Telegram bot interface for RedClaw.

Features:
- Text messages → agent processes → reply
- File upload/download
- Slash commands
- Per-user sessions
- Typing indicator
- Message splitting for long responses
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from redclaw.api.client import LLMClient
from redclaw.api.providers import get_provider
from redclaw.api.types import Usage
from redclaw.runtime.compact import compact_session
from redclaw.runtime.conversation import ConversationCallbacks, ConversationRuntime, TurnSummary
from redclaw.runtime.permissions import PermissionMode, PermissionPolicy
from redclaw.runtime.session import Session
from redclaw.runtime.usage import UsageTracker
from redclaw.tools.registry import ToolExecutor

logger = logging.getLogger(__name__)

MAX_MSG_LEN = 4096


def _split_message(text: str) -> list[str]:
    """Split text into chunks that fit within Telegram's message limit."""
    if len(text) <= MAX_MSG_LEN:
        return [text]
    chunks = []
    while text:
        if len(text) <= MAX_MSG_LEN:
            chunks.append(text)
            break
        # Try to split at a newline
        split_at = text.rfind("\n", 0, MAX_MSG_LEN)
        if split_at < MAX_MSG_LEN // 2:
            split_at = MAX_MSG_LEN
        chunks.append(text[:split_at])
        text = text[split_at:]
    return chunks


class TelegramSession:
    """Per-user session state."""

    def __init__(self, user_id: int, working_dir: str, provider_name: str,
                 model: str, base_url: str | None, perm_mode: str,
                 search_url: str | None = None, reader_url: str | None = None,
                 mcp_servers: list[str] | None = None,
                 assistant_mode: bool = False):
        self.user_id = user_id
        self.working_dir = working_dir
        self.provider_name = provider_name
        self.model = model
        self.mcp_servers = mcp_servers or []
        self.assistant_mode = assistant_mode

        cwd = working_dir
        self.provider = get_provider(provider_name, base_url)
        self.client = LLMClient(self.provider)

        self.session = Session(id=f"tg-{user_id}")
        self.session.model = model
        self.session.provider = provider_name
        self.session.working_dir = cwd

        self.tools = ToolExecutor(working_dir=cwd, search_url=search_url, reader_url=reader_url)
        self.mcp_client: Any = None
        self.policy = PermissionPolicy(mode=PermissionMode(perm_mode))
        self.tracker = UsageTracker()

        # Assistant stores (only created if assistant_mode)
        self.tasks_store = None
        self.notes_store = None
        self.reminders_store = None

        # Build assistant context for system prompt
        assistant_context = ""
        if assistant_mode:
            from redclaw.assistant.tasks import TaskStore
            from redclaw.assistant.notes import NoteStore
            from redclaw.assistant.reminders import ReminderStore
            from redclaw.assistant.config import AssistantConfig
            from redclaw.tools.assistant_tools import set_stores
            from datetime import datetime, timezone

            self.tasks_store = TaskStore()
            self.notes_store = NoteStore()
            self.reminders_store = ReminderStore()
            self.assistant_config = AssistantConfig.load()

            # Share store instances with tool functions
            set_stores(self.tasks_store, self.notes_store, self.reminders_store)

            # Register assistant tools
            self._register_assistant_tools()

            # AGI subsystem — project-managing meeseek subagents
            self._init_agi(self.client, self.provider, self.model)

            # Build context
            pending_tasks = len(self.tasks_store.list_tasks(status="pending"))
            pending_reminders = len(self.reminders_store.get_pending())
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            assistant_context = (
                f"Current time: {now}\n"
                f"Pending tasks: {pending_tasks}\n"
                f"Pending reminders: {pending_reminders}\n"
            )

            # Inject AGI status into assistant context
            if hasattr(self, 'agi_executive') and self.agi_executive:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Can't await in __init__ during running loop — inject placeholder
                        assistant_context += "\nAGI Goals: Loading...\n"
                    else:
                        agi_status = loop.run_until_complete(self.agi_executive.get_status_summary())
                        if agi_status:
                            assistant_context += f"\nAGI Goals:\n{agi_status}\n"
                except RuntimeError:
                    pass

            persona = self.assistant_config.persona_name
            if persona:
                assistant_context = f"Your name is {persona}.\n" + assistant_context

        self.rt = ConversationRuntime(
            client=self.client,
            provider=self.provider,
            model=model,
            session=self.session,
            tools=self.tools,
            permission_policy=self.policy,
            usage_tracker=self.tracker,
            working_dir=cwd,
            mode="assistant" if assistant_mode else "coder",
            assistant_context=assistant_context,
        )
        self.current_task: asyncio.Task | None = None
        self.pending_context: list[str] = []  # Queued messages to inject after current task

    def _register_assistant_tools(self) -> None:
        """Register task, note, reminder, and knowledge graph tools."""
        from redclaw.api.types import PermissionLevel
        from redclaw.tools.registry import ToolSpec
        from redclaw.tools.assistant_tools import execute_task, execute_note, execute_reminder
        from redclaw.memory_graph.tools import execute_knowledge

        self.tools.register_tool(ToolSpec(
            name="task",
            description="Manage tasks. Actions: add, list, update, delete, search. Use this to track to-dos and action items.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action: add, list, update, delete, search", "default": "list"},
                    "title": {"type": "string", "description": "Task title (for add/update)"},
                    "task_id": {"type": "string", "description": "Task ID (for update/delete)"},
                    "description": {"type": "string", "description": "Task description"},
                    "priority": {"type": "string", "description": "Priority: low, medium, high, urgent", "default": "medium"},
                    "due": {"type": "string", "description": "Due date (ISO 8601, e.g. 2025-01-15T09:00:00)"},
                    "tags": {"type": "string", "description": "Comma-separated tags"},
                    "status": {"type": "string", "description": "Status: pending, in_progress, done, cancelled"},
                    "query": {"type": "string", "description": "Search query (for search) or tag filter (for list)"},
                },
                "required": [],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=execute_task,
        ))
        self.tools.register_tool(ToolSpec(
            name="note",
            description="Manage notes. Actions: add, list, update, delete, search. Use this to save information and knowledge.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action: add, list, update, delete, search", "default": "list"},
                    "title": {"type": "string", "description": "Note title (for add/update)"},
                    "note_id": {"type": "string", "description": "Note ID (for update/delete)"},
                    "content": {"type": "string", "description": "Note content (markdown)"},
                    "tags": {"type": "string", "description": "Comma-separated tags"},
                    "source": {"type": "string", "description": "Source of the note"},
                    "query": {"type": "string", "description": "Search query (for search) or tag filter (for list)"},
                },
                "required": [],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=execute_note,
        ))
        self.tools.register_tool(ToolSpec(
            name="reminder",
            description="Manage reminders. Actions: add, list, delete. Use this to set time-based alerts.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action: add, list, delete", "default": "list"},
                    "text": {"type": "string", "description": "Reminder text (for add)"},
                    "trigger_at": {"type": "string", "description": "Trigger time in ISO 8601 (e.g. 2025-01-15T09:00:00)"},
                    "recurrence": {"type": "string", "description": "Recurrence: daily, weekly, monthly, weekdays, or empty"},
                    "reminder_id": {"type": "string", "description": "Reminder ID (for delete)"},
                },
                "required": [],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=execute_reminder,
        ))
        self.tools.register_tool(ToolSpec(
            name="knowledge",
            description=(
                "Knowledge graph memory powered by Cognee. Builds structured entity/relationship graphs from text. "
                "Actions: add (ingest text), cognify (build graph), search (query graph), memify (enrich), "
                "stats, list, delete, prune, visualize. "
                "Use 'add' to ingest text, then 'cognify' to extract entities and build the graph, then 'search' to query."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: add, cognify, search, memify, stats, list, delete, prune, visualize",
                        "default": "stats",
                    },
                    "data": {
                        "type": "string",
                        "description": "Data to ingest (text, file path, or URL) for 'add' action",
                    },
                    "dataset_name": {
                        "type": "string",
                        "description": "Named dataset to group data (default: redclaw_memory)",
                        "default": "redclaw_memory",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for 'search' action",
                    },
                    "search_type": {
                        "type": "string",
                        "description": "Search strategy: GRAPH_COMPLETION (default), RAG_COMPLETION, CHUNKS, SUMMARIES, FEELING_LUCKY",
                        "default": "GRAPH_COMPLETION",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Max results for search (default: 5)",
                        "default": 5,
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "Run cognify in background for large datasets",
                        "default": False,
                    },
                },
                "required": [],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=execute_knowledge,
        ))

    def _init_agi(self, client: Any, provider: Any, model: str) -> None:
        """Initialize AGI subsystem for project-managing meeseek subagents.

        Wires up: Crypt, DNAManager, DreamSynthesizer, EventBus, KarmaObserver,
        SubagentSpawner, and AutonomousExecutive. Registers execute_goal and
        subagent tools so the assistant can create goals and delegate work.
        """
        from redclaw.crypt.crypt import Crypt
        from redclaw.crypt.dna import DNAManager
        from redclaw.crypt.dream import DreamSynthesizer
        from redclaw.crypt.karma import KarmaObserver
        from redclaw.runtime.event_bus import EventBus, EventLogger
        from redclaw.runtime.soul import load_soul, verify_soul_integrity
        from redclaw.runtime.subagent import SubagentSpawner, execute_subagent
        from redclaw.runtime.autonomous import AutonomousExecutive
        from redclaw.tools.agi_tools import register_agi_tools
        from redclaw.api.types import PermissionLevel
        from redclaw.tools.registry import ToolSpec
        import asyncio

        cwd = self.working_dir

        # Load SOUL constitution
        soul_text = load_soul(cwd)
        verify_soul_integrity(soul_text, cwd)
        self.soul_text = soul_text

        # Core AGI subsystems
        dna_manager = DNAManager()
        crypt = Crypt(dna_manager=dna_manager)
        dream = DreamSynthesizer(client, provider, model)
        crypt._dream_synthesizer = dream  # wire dream trigger into entomb

        event_bus = EventBus()
        event_bus.subscribe(EventLogger())
        karma = KarmaObserver(soul_text, event_bus)
        event_bus.subscribe(karma)

        # Subagent spawner with DNA awareness
        spawner = SubagentSpawner(
            client=client,
            provider=provider,
            model=model,
            tools=self.tools,
            crypt=crypt,
            dna_manager=dna_manager,
        )
        self.subagent_spawner = spawner

        # Register subagent tool (delegates work to meeseek subagents)
        self.tools.register_tool(ToolSpec(
            name="subagent",
            description=(
                "Delegate a task to an isolated meeseek sub-agent. "
                "Provide a single task or newline-separated tasks for batch. "
                "subagent_type: 'coder' (code changes), 'searcher' (find info), or 'general'. "
                "Use this to parallelize work across specialized subagents."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Single task description"},
                    "tasks": {"type": "string", "description": "Newline-separated tasks for batch execution"},
                    "subagent_type": {
                        "type": "string",
                        "description": "Type: coder, searcher, general (default: general)",
                        "default": "general",
                    },
                },
                "required": ["task"],
            },
            permission=PermissionLevel.WORKSPACE_WRITE,
            execute=lambda **kw: execute_subagent(spawner=spawner, **kw),
        ))

        # Register goal management tool
        register_agi_tools(self.tools, event_bus)

        # Autonomous executive (background goal-pursuing loop)
        self.agi_executive = AutonomousExecutive(
            client=client,
            provider=provider,
            model=model,
            tools=self.tools,
            spawner=spawner,
            crypt=crypt,
            dna_manager=dna_manager,
            dream_synthesizer=dream,
            event_bus=event_bus,
            soul_text=soul_text,
            working_dir=cwd,
            interval=120,  # 2min in assistant mode
        )

        # Start executive as background task
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.agi_executive.run())
            logger.info("AGI executive started for user %s", self.user_id)
        except RuntimeError:
            logger.warning("Could not start AGI executive — no event loop")

        logger.info("AGI subsystem initialized for user %s (DNA + Dream + Karma + Executive)", self.user_id)

    async def init_mcp(self) -> None:
        """Discover and register MCP tools, retrying failed servers."""
        if not self.mcp_servers:
            return
        from redclaw.mcp_client import MCPClient, MCPServerConfig
        configs = [MCPServerConfig(name=f"mcp-{i}", url=url) for i, url in enumerate(self.mcp_servers)]
        self.mcp_client = MCPClient(configs)

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                tools = await self.mcp_client.discover()
                for tool in tools:
                    from redclaw.api.types import PermissionLevel
                    from redclaw.tools.registry import ToolSpec
                    spec = ToolSpec(
                        name=tool.name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                        permission=PermissionLevel.READ_ONLY,
                        execute=lambda *args, _name=tool.name, **kw: self.mcp_client.call_tool(_name, kw),
                    )
                    self.tools.specs[tool.name] = spec
                logger.info(f"Registered {len(tools)} MCP tools for user {self.user_id}")
            except Exception as e:
                logger.error(f"MCP discovery failed for user {self.user_id}: {e}")

            # Check if all servers connected
            connected = len(self.mcp_client._connections)
            if connected >= len(configs):
                break
            if attempt < max_attempts:
                logger.info(f"MCP: {connected}/{len(configs)} servers connected, retrying in 5s...")
                await asyncio.sleep(5)
                # Reset failed connections for retry
                self.mcp_client = MCPClient(configs)

    async def close(self) -> None:
        await self.client.close()


class RedClawTelegramBot:
    """Telegram bot for RedClaw."""

    def __init__(
        self,
        token: str,
        allowed_user_id: int | None = None,
        working_dir: str | None = None,
        provider_name: str = "zai",
        model: str = "glm-4.7",
        base_url: str | None = None,
        perm_mode: str = "ask",
        search_url: str | None = None,
        reader_url: str | None = None,
        mcp_servers: list[str] | None = None,
        assistant_mode: bool = False,
    ):
        self.token = token
        self.allowed_user_id = allowed_user_id
        # Default to RedClaw home
        self.working_dir = working_dir or str(Path.home() / ".redclaw")
        Path(self.working_dir).mkdir(parents=True, exist_ok=True)
        self.provider_name = provider_name
        self.model = model
        self.base_url = base_url
        self.perm_mode = perm_mode
        self.search_url = search_url
        self.reader_url = reader_url
        self.mcp_servers = mcp_servers or []
        self.assistant_mode = assistant_mode
        self.sessions: dict[int, TelegramSession] = {}
        self._app: Any = None
        self._schedulers: dict[int, Any] = {}

    def _get_session(self, user_id: int) -> TelegramSession:
        if user_id not in self.sessions:
            self.sessions[user_id] = TelegramSession(
                user_id=user_id,
                working_dir=self.working_dir,
                provider_name=self.provider_name,
                model=self.model,
                base_url=self.base_url,
                perm_mode=self.perm_mode,
                search_url=self.search_url,
                reader_url=self.reader_url,
                mcp_servers=self.mcp_servers,
                assistant_mode=self.assistant_mode,
            )
            # Schedule MCP discovery in background
            asyncio.create_task(self.sessions[user_id].init_mcp())
            # Start assistant scheduler if in assistant mode
            if self.assistant_mode and self._app is not None:
                self._start_scheduler(user_id)
        return self.sessions[user_id]

    def _check_user(self, update: Update) -> bool:
        if self.allowed_user_id is None:
            return True
        return update.effective_user.id == self.allowed_user_id

    async def _send_reply(self, update: Update, text: str) -> None:
        """Send a reply, splitting into multiple messages if needed."""
        for chunk in _split_message(text):
            try:
                await update.message.reply_text(chunk, parse_mode=None)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")

    # ── Command Handlers ─────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        assistant_cmds = ""
        if self.assistant_mode:
            assistant_cmds = (
                "\nAssistant:\n"
                "/task <title> — Quick add task\n"
                "/tasks — List pending tasks\n"
                "/taskdone <id> — Mark task done\n"
                "/note <title> — Quick add note\n"
                "/notes — List recent notes\n"
                "/remind <time> <text> — Set reminder\n"
                "/reminders — List pending reminders\n"
                "/briefing — Get daily briefing now\n"
                "/knowledge [action] [args] — Knowledge graph memory\n"
                "/config [key=val] — View/set assistant config\n"
            )
        await self._send_reply(update, (
            "*RedClaw* — AI Coding Agent\n\n"
            "Send me a message and I'll process it with my coding tools.\n\n"
            "Commands:\n"
            "/help — List all commands\n"
            "/usage — Token usage stats\n"
            "/session — Current session info\n"
            "/new — New session\n"
            "/clear — Clear session history\n"
            "/compact — Compact history\n"
            "/model [name] — Show/set model\n"
            "/provider [name] — Show/set provider\n"
            "/perms [mode] — Show/set permission mode\n"
            "/memory [query] — Recall/search memories\n"
            "/skills — List available skills\n"
            "/crypt — Wisdom inheritance stats\n"
            "/abort — Abort current turn\n"
            "/plan — Enter plan mode (read-only)\n"
            "/go — Execute plan from .redclaw.md\n"
            "/init — Create .redclaw.md with project context\n"
            "/new <name> — Create project folder and switch to it\n"
            "/get <path> — Download file\n"
            "/getzip <path> — Download directory as zip\n"
            "/ls [path] — List files\n"
            "/run <cmd> — Direct bash command\n"
            "/files — List uploaded files\n"
            "/status — Connection + agent status\n"
            "/restart — Restart bot (reloads MCP servers)"
            + assistant_cmds
        ))

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        assistant_cmds = ""
        if self.assistant_mode:
            assistant_cmds = (
                "\nAssistant:\n"
                "/task <title> — Quick add task\n"
                "/tasks — List pending tasks\n"
                "/taskdone <id> — Mark task done\n"
                "/note <title> — Quick add note\n"
                "/notes — List recent notes\n"
                "/remind <time> <text> — Set reminder\n"
                "/reminders — List pending reminders\n"
                "/briefing — Get daily briefing now\n"
                "/knowledge [action] [args] — Knowledge graph memory\n"
                "/config [key=val] — View/set assistant config\n\n"
            )
        await self._send_reply(update, (
            "RedClaw Commands:\n\n"
            "/start — Welcome + instructions\n"
            "/help — This help message\n"
            "/usage — Token usage stats\n"
            "/session — Current session info\n"
            "/new — Start a new session\n"
            "/clear — Clear session history\n"
            "/compact — Compact conversation history\n"
            "/model [name] — Show or set model\n"
            "/provider [name] — Show or set provider\n"
            "/perms [mode] — Show or set permission mode\n"
            "/memory [query] — Recall or search memories\n"
            "/skills — List available skills\n"
            "/crypt — Wisdom inheritance stats\n"
            "/abort — Abort current turn\n"
            "/plan — Enter plan mode (read-only)\n"
            "/go — Execute plan from .redclaw.md\n"
            "/init — Create .redclaw.md with project context\n"
            "/new <name> — Create project folder and switch to it\n"
            "/get <path> — Download a file\n"
            "/getzip <path> — Download directory as zip\n"
            "/ls [path] — List files in directory\n"
            "/run <cmd> — Run a bash command (bypasses LLM)\n"
            "/files — List uploaded files\n"
            "/status — Connection and agent status\n\n"
            + assistant_cmds
            + "Just type a message to chat with the agent.\n"
            "Send any file to upload it to the working directory."
        ))

    async def cmd_usage(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        await self._send_reply(update, s.tracker.summary())

    async def cmd_session(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        await self._send_reply(update, (
            f"Session: {s.session.id}\n"
            f"Model: {s.rt.model}\n"
            f"Provider: {s.provider_name}\n"
            f"Messages: {len(s.session.messages)}\n"
            f"Working dir: {s.working_dir}"
        ))

    async def cmd_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        uid = update.effective_user.id
        if uid in self.sessions:
            old = self.sessions.pop(uid)
            await old.close()
        s = self._get_session(uid)
        await self._send_reply(update, f"New session started: {s.session.id}")

    async def cmd_compact(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        compact_session(s.session)
        await self._send_reply(update, "Session compacted.")

    async def cmd_clear(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        s.session.messages.clear()
        await self._send_reply(update, "Session history cleared.")

    async def cmd_plan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        s.rt.set_plan_mode(True)
        await self._send_reply(update, "PLAN MODE — explore & write .redclaw.md. Use /go to execute.")

    async def cmd_go(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        if s.rt.plan_mode:
            s.rt.set_plan_mode(False)
            await self._send_reply(update, "EXECUTE MODE — reading .redclaw.md, executing now.")
        else:
            await self._send_reply(update, "Not in plan mode. Use /plan first.")

    async def cmd_init(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        from redclaw.runtime.prompt import _init_redclaw_md
        content = _init_redclaw_md(s.working_dir)
        await self._send_reply(update, f".redclaw.md created ({len(content)} chars)")

    async def cmd_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Create a new project folder and switch to it."""
        if not self._check_user(update):
            return
        text = (update.message.text or "").strip()
        parts = text.split(maxsplit=1)
        name = parts[1] if len(parts) > 1 else ""
        if not name:
            await self._send_reply(update, "Usage: /new <project-name>")
            return

        # Create project dir
        from pathlib import Path
        projects_dir = Path.home() / ".redclaw" / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        project_dir = projects_dir / name
        project_dir.mkdir(exist_ok=True)

        # Switch session working dir
        uid = update.effective_user.id
        s = self._get_session(uid)
        old_dir = s.working_dir
        s.working_dir = str(project_dir)
        s.rt.working_dir = str(project_dir)
        s.session.working_dir = str(project_dir)

        # Init .redclaw.md in project
        from redclaw.runtime.prompt import _init_redclaw_md
        content = _init_redclaw_md(str(project_dir))
        await self._send_reply(update, f"Project '{name}' created.\n.redclaw.md initialized.\nDir: {project_dir}")

    async def cmd_provider(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            new_provider = parts[1].strip()
            try:
                from redclaw.api.providers import get_provider
                s.provider = get_provider(new_provider, None)
                s.provider_name = new_provider
                await self._send_reply(update, f"Provider set to: {new_provider}")
            except Exception as e:
                await self._send_reply(update, f"Error setting provider: {e}")
        else:
            await self._send_reply(update, f"Current provider: {s.provider_name}")

    async def cmd_perms(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        valid_modes = ["ask", "read_only", "workspace_write", "danger_full_access"]
        if len(parts) > 1:
            new_mode = parts[1].strip()
            if new_mode not in valid_modes:
                await self._send_reply(update, f"Invalid mode. Options: {', '.join(valid_modes)}")
                return
            from redclaw.runtime.permissions import PermissionMode
            s.policy.mode = PermissionMode(new_mode)
            await self._send_reply(update, f"Permission mode set to: {new_mode}")
        else:
            await self._send_reply(update, f"Permission mode: {s.policy.mode.value}")

    async def cmd_memory(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        try:
            from redclaw.tools.memory import get_memory_manager
            mgr = get_memory_manager()
            text = update.message.text or ""
            parts = text.split(maxsplit=1)
            query = parts[1].strip() if len(parts) > 1 else ""
            if query:
                result = await mgr.recall(query)
            else:
                result = await mgr.recall()
            await self._send_reply(update, result)
        except Exception as e:
            await self._send_reply(update, f"Memory error: {e}")

    async def cmd_skills(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        try:
            from redclaw.skills.agent_tools import execute_skills_list
            result = await execute_skills_list()
            await self._send_reply(update, result)
        except ImportError:
            await self._send_reply(update, "Skills system not available (PyYAML required).")
        except Exception as e:
            await self._send_reply(update, f"Skills error: {e}")

    async def cmd_crypt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        try:
            from redclaw.crypt import Crypt
            crypt = Crypt()
            m = crypt.metrics
            lines = [
                f"Crypt Metrics:",
                f"  Total tasks: {m.tasks_total}",
                f"  Success: {m.tasks_success} | Failed: {m.tasks_failed}",
            ]
            if m.by_type:
                for type_name, stats in m.by_type.items():
                    rate = (stats['success'] / stats['total'] * 100) if stats['total'] else 0
                    lines.append(f"  {type_name}: {stats['total']} tasks, {rate:.0f}% success")
            # Show dharma preview
            dharma = crypt.load_dharma()
            if dharma:
                preview = dharma.strip().split("\n")[-3:]
                lines.append(f"\nRecent dharma:")
                lines.extend(f"  {l.strip()}" for l in preview if l.strip())
            if len(lines) <= 3:
                lines.append("\nNo crypt data yet. Subagent runs will populate this.")
            await self._send_reply(update, "\n".join(lines))
        except Exception as e:
            await self._send_reply(update, f"Crypt error: {e}")

    async def cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            new_model = parts[1].strip()
            s.rt.model = new_model
            await self._send_reply(update, f"Model set to: {new_model}")
        else:
            await self._send_reply(update, f"Current model: {s.rt.model}")

    async def cmd_abort(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        s.rt.abort()
        if s.current_task and not s.current_task.done():
            s.current_task.cancel()
        await self._send_reply(update, "Turn aborted.")

    async def cmd_get(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await self._send_reply(update, "Usage: /get <path>")
            return
        rel_path = parts[1].strip()
        file_path = Path(self.working_dir) / rel_path
        # Security: ensure path is within working_dir
        try:
            file_path.resolve().relative_to(Path(self.working_dir).resolve())
        except ValueError:
            await self._send_reply(update, "Error: path must be within working directory")
            return
        if not file_path.is_file():
            await self._send_reply(update, f"Error: file not found: {rel_path}")
            return
        try:
            await update.message.reply_document(document=open(file_path, "rb"), filename=file_path.name)
        except Exception as e:
            await self._send_reply(update, f"Error sending file: {e}")

    async def cmd_getzip(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await self._send_reply(update, "Usage: /getzip <path>")
            return
        rel_path = parts[1].strip()
        dir_path = Path(self.working_dir) / rel_path
        try:
            dir_path.resolve().relative_to(Path(self.working_dir).resolve())
        except ValueError:
            await self._send_reply(update, "Error: path must be within working directory")
            return
        if not dir_path.is_dir():
            await self._send_reply(update, f"Error: directory not found: {rel_path}")
            return
        # Create zip in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in dir_path.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(dir_path))
        buf.seek(0)
        zip_name = f"{dir_path.name}.zip"
        try:
            await update.message.reply_document(document=buf, filename=zip_name)
        except Exception as e:
            await self._send_reply(update, f"Error sending zip: {e}")

    async def cmd_ls(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        rel_path = parts[1].strip() if len(parts) > 1 else "."
        target = Path(self.working_dir) / rel_path
        try:
            target.resolve().relative_to(Path(self.working_dir).resolve())
        except ValueError:
            await self._send_reply(update, "Error: path must be within working directory")
            return
        if not target.is_dir():
            await self._send_reply(update, f"Error: not a directory: {rel_path}")
            return
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        lines = []
        for e in entries[:50]:
            prefix = "DIR " if e.is_dir() else "     "
            lines.append(f"{prefix} {e.name}")
        if len(entries) > 50:
            lines.append(f"... ({len(entries)} entries total)")
        result = "\n".join(lines) if lines else "(empty directory)"
        await self._send_reply(update, f"```\n{result}\n```")

    async def cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await self._send_reply(update, "Usage: /run <command>")
            return
        cmd = parts[1].strip()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            result = stdout.decode(errors="replace")
            if stderr:
                result += "\n" + stderr.decode(errors="replace")
            if not result.strip():
                result = "(no output)"
            await self._send_reply(update, f"```\n{result}\n```")
        except asyncio.TimeoutError:
            await self._send_reply(update, "Command timed out (120s)")
        except Exception as e:
            await self._send_reply(update, f"Error: {e}")

    async def cmd_files(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        upload_dir = Path(self.working_dir) / "uploads"
        if not upload_dir.is_dir():
            await self._send_reply(update, "No uploads directory yet.")
            return
        files = sorted(upload_dir.iterdir(), key=lambda p: p.name)
        if not files:
            await self._send_reply(update, "No uploaded files.")
            return
        lines = [f"  {f.name} ({f.stat().st_size:,} bytes)" for f in files[:30]]
        await self._send_reply(update, "Uploaded files:\n" + "\n".join(lines))

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        busy = s.current_task is not None and not s.current_task.done()
        mcp_count = len(s.mcp_servers) if s.mcp_servers else 0
        await self._send_reply(update, (
            f"Status: {'busy' if busy else 'idle'}\n"
            f"Session: {s.session.id}\n"
            f"Model: {s.rt.model}\n"
            f"Provider: {s.provider_name}\n"
            f"Messages: {len(s.session.messages)}\n"
            f"MCP servers: {mcp_count}\n"
            f"{s.tracker.summary()}"
        ))

    async def cmd_restart(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        await self._send_reply(update, "Restarting bot... See you on the other side!")
        # Close all sessions
        for s in self.sessions.values():
            try:
                await s.close()
            except Exception:
                pass
        self.sessions.clear()
        # Re-exec the process with the same arguments
        os.execv(sys.executable, sys.argv)

    # ── Message Handlers ─────────────────────────────────────

    async def on_text_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = (update.message.text or "").strip()
        if not text:
            return

        s = self._get_session(update.effective_user.id)

        # Queue message if already processing (don't lose it)
        if s.current_task and not s.current_task.done():
            s.pending_context.append(text)
            try:
                await update.message.set_reaction("📥")
            except Exception:
                pass
            return

        # Prepend any queued "by the way" messages to this one
        if s.pending_context:
            queued = s.pending_context.copy()
            s.pending_context.clear()
            text = "[User sent while you were busy — address these too]:\n" + "\n".join(queued) + "\n\n[Latest message]:\n" + text

        # React with processing indicator
        try:
            await update.message.set_reaction("⚡")
        except Exception:
            pass

        async def _process() -> None:
            collected_text = ""
            tool_names: list[str] = []
            created_files: list[str] = []
            status_msg = None

            async def on_text_delta(t: str) -> None:
                nonlocal collected_text
                collected_text += t

            async def on_tool_begin(tid: str, name: str, inp: str) -> None:
                nonlocal status_msg
                tool_names.append(name)
                try:
                    tool_list = " | ".join(f"▶ {n}" for n in tool_names)
                    if status_msg is None:
                        status_msg = await update.message.reply_text(tool_list)
                    else:
                        await status_msg.edit_text(tool_list)
                except Exception:
                    pass

            async def on_tool_result(tid: str, result: str, is_error: bool) -> None:
                if not is_error and result.startswith("Wrote ") and " bytes to " in result:
                    path = result.split(" bytes to ", 1)[1].strip()
                    created_files.append(path)

            async def on_usage(u: Usage) -> None:
                pass

            async def on_error(msg: str) -> None:
                try:
                    await update.message.reply_text(f"Error: {msg}")
                except Exception:
                    pass

            cb = ConversationCallbacks(
                on_text_delta=on_text_delta,
                on_tool_begin=on_tool_begin,
                on_tool_result=on_tool_result,
                on_usage=on_usage,
                on_error=on_error,
            )

            # Typing indicator
            async def _keep_typing() -> None:
                while True:
                    try:
                        await update.message.chat.send_action("typing")
                    except Exception:
                        pass
                    await asyncio.sleep(4)

            typing_task = asyncio.create_task(_keep_typing())
            try:
                summary = await s.rt.run_turn(text, cb)
            finally:
                typing_task.cancel()

            # Send response
            reply = collected_text.strip() if collected_text.strip() else "(no text response)"
            if summary.error:
                reply = f"Error: {summary.error}\n\n{reply}"
            await self._send_reply(update, reply)

            # Auto-send files created by tools as Telegram documents
            for fp in created_files:
                try:
                    p = Path(fp)
                    if p.is_file():
                        with open(p, "rb") as doc:
                            await update.message.reply_document(document=doc, filename=p.name)
                except Exception:
                    pass

            # Update reaction to done
            try:
                await update.message.set_reaction("✅")
            except Exception:
                pass

        s.current_task = asyncio.create_task(_process())

    async def on_file_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)

        # Get the file
        doc = update.message.document
        photo = update.message.photo
        voice = update.message.voice
        audio = update.message.audio
        video = update.message.video

        file_obj = None
        filename = None

        if doc:
            file_obj = doc
            filename = doc.file_name or f"file_{doc.file_id[:8]}"
        elif photo:
            file_obj = photo[-1]  # Largest resolution
            filename = f"photo_{update.message.message_id}.jpg"
        elif voice:
            file_obj = voice
            filename = f"voice_{update.message.message_id}.ogg"
        elif audio:
            file_obj = audio
            filename = audio.file_name or f"audio_{update.message.message_id}"
        elif video:
            file_obj = video
            file_name = video.file_name or f"video_{update.message.message_id}.mp4"

        if not file_obj:
            await self._send_reply(update, "Could not extract file from message.")
            return

        # Download
        upload_dir = Path(self.working_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / filename

        try:
            tg_file = await file_obj.get_file()
            await tg_file.download_to_drive(str(dest))
            size = dest.stat().st_size
            await self._send_reply(update, f"File uploaded: {filename} ({size:,} bytes)")

            # Notify the agent about the uploaded file
            if s.current_task and not s.current_task.done():
                # If agent is busy, just notify
                pass
            # Add file upload as a system message to the session
            from redclaw.api.types import InputMessage, Role, TextBlock
            s.session.add_message(InputMessage(
                role=Role.USER,
                content=[TextBlock(text=f"[File uploaded: {filename} ({size:,} bytes) -> uploads/{filename}]")],
            ))
        except Exception as e:
            await self._send_reply(update, f"Error downloading file: {e}")

    # ── Proactive messaging ──────────────────────────────────

    async def send_proactive(self, user_id: int, text: str) -> None:
        """Send a proactive message to a user via Telegram."""
        if self._app is None:
            return
        try:
            await self._app.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logger.error(f"Failed to send proactive message to {user_id}: {e}")

    def _start_scheduler(self, user_id: int) -> None:
        """Start the assistant scheduler for a user."""
        s = self.sessions.get(user_id)
        if not s or not s.assistant_mode:
            return
        if user_id in self._schedulers:
            return
        from redclaw.assistant.scheduler import AssistantScheduler
        scheduler = AssistantScheduler(
            config=s.assistant_config,
            tasks=s.tasks_store,
            reminders=s.reminders_store,
            send_fn=self.send_proactive,
            user_id=user_id,
            search_url=self.search_url,
        )
        scheduler.start()
        self._schedulers[user_id] = scheduler

    # ── Assistant Commands ────────────────────────────────────

    async def cmd_task(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await self._send_reply(update, "Usage: /task <title>")
            return
        s = self._get_session(update.effective_user.id)
        task = s.tasks_store.add(title=parts[1].strip())
        await self._send_reply(update, f"Task added: {task.id} — {task.title}")

    async def cmd_tasks(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        tasks = s.tasks_store.list_tasks(status="pending")
        if not tasks:
            await self._send_reply(update, "No pending tasks.")
            return
        lines = []
        for t in tasks:
            marker = {"urgent": "!!!", "high": "!!", "medium": "!", "low": "-"}.get(t.priority, "!")
            lines.append(f"{marker} {t.id} | {t.title}" + (f" (due {t.due})" if t.due else ""))
        await self._send_reply(update, "Pending tasks:\n" + "\n".join(lines))

    async def cmd_taskdone(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await self._send_reply(update, "Usage: /taskdone <id>")
            return
        s = self._get_session(update.effective_user.id)
        result = s.tasks_store.update(parts[1].strip(), status="done")
        if result:
            await self._send_reply(update, f"Task done: {result.title}")
        else:
            await self._send_reply(update, f"Task '{parts[1].strip()}' not found.")

    async def cmd_note(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await self._send_reply(update, "Usage: /note <title>")
            return
        s = self._get_session(update.effective_user.id)
        note = s.notes_store.add(title=parts[1].strip())
        await self._send_reply(update, f"Note added: {note.id} — {note.title}")

    async def cmd_notes(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        notes = s.notes_store.list_notes(limit=10)
        if not notes:
            await self._send_reply(update, "No notes yet.")
            return
        lines = [f"- {n.id} | {n.title}" for n in notes]
        await self._send_reply(update, "Recent notes:\n" + "\n".join(lines))

    async def cmd_remind(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await self._send_reply(update, "Usage: /remind <ISO datetime> <text>\nExample: /remind 2025-01-15T09:00 Call mom")
            return
        s = self._get_session(update.effective_user.id)
        trigger_at = parts[1].strip()
        reminder_text = parts[2].strip()
        try:
            reminder = s.reminders_store.add(text=reminder_text, trigger_at=trigger_at)
            await self._send_reply(update, f"Reminder set: {reminder.id} — {reminder_text} at {trigger_at}")
        except Exception as e:
            await self._send_reply(update, f"Error: {e}")

    async def cmd_reminders(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        pending = s.reminders_store.get_pending()
        if not pending:
            await self._send_reply(update, "No pending reminders.")
            return
        lines = [f"- {r.id} | {r.text} (at {r.trigger_at})" for r in pending]
        await self._send_reply(update, "Pending reminders:\n" + "\n".join(lines))

    async def cmd_briefing(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        if not s.assistant_mode:
            await self._send_reply(update, "Assistant mode not enabled. Use --assistant flag.")
            return
        try:
            from redclaw.assistant.briefing import BriefingGenerator
            gen = BriefingGenerator(
                config=s.assistant_config,
                tasks=s.tasks_store,
                reminders=s.reminders_store,
                search_url=self.search_url,
            )
            briefing = await gen.generate()
            await self._send_reply(update, briefing)
        except Exception as e:
            await self._send_reply(update, f"Briefing error: {e}")

    async def cmd_config(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        if not s.assistant_mode:
            await self._send_reply(update, "Assistant mode not enabled. Use --assistant flag.")
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            # Parse key=value pairs
            assignment = parts[1].strip()
            if "=" not in assignment:
                await self._send_reply(update, "Usage: /config key=value\nKeys: timezone, briefing_time, briefing_enabled, weather_location, briefing_weather, briefing_news, briefing_tasks")
                return
            key, val = assignment.split("=", 1)
            key = key.strip()
            val = val.strip()
            cfg = s.assistant_config
            if key == "timezone":
                cfg.timezone = val
            elif key == "briefing_time":
                cfg.briefing_time = val
            elif key == "briefing_enabled":
                cfg.briefing_enabled = val.lower() in ("true", "1", "yes")
            elif key == "weather_location":
                cfg.weather_location = val
            elif key == "briefing_weather":
                cfg.briefing_weather = val.lower() in ("true", "1", "yes")
            elif key == "briefing_news":
                cfg.briefing_news = val.lower() in ("true", "1", "yes")
            elif key == "briefing_tasks":
                cfg.briefing_tasks = val.lower() in ("true", "1", "yes")
            else:
                await self._send_reply(update, f"Unknown key: {key}")
                return
            cfg.save()
            await self._send_reply(update, f"Config updated: {key}={val}")
        else:
            from dataclasses import asdict
            cfg = s.assistant_config
            data = {k: v for k, v in asdict(cfg).items() if k != "_path"}
            lines = [f"{k}: {v}" for k, v in data.items()]
            await self._send_reply(update, "Assistant config:\n" + "\n".join(lines))

    async def cmd_knowledge(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Knowledge graph slash command: /knowledge <action> [args]."""
        if not self._check_user(update):
            return
        s = self._get_session(update.effective_user.id)
        if not s.assistant_mode:
            await self._send_reply(update, "Assistant mode not enabled. Use --assistant flag.")
            return

        from redclaw.memory_graph.tools import execute_knowledge

        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            result = await execute_knowledge(action="stats")
            await self._send_reply(update, result)
            return

        arg = parts[1].strip()

        # Quick shortcuts: /knowledge add <text>, /knowledge search <query>
        if arg.startswith("add "):
            data = arg[4:].strip()
            result = await execute_knowledge(action="add", data=data)
        elif arg.startswith("search "):
            query = arg[7:].strip()
            result = await execute_knowledge(action="search", query=query)
        elif arg == "cognify":
            result = await execute_knowledge(action="cognify")
        elif arg == "memify":
            result = await execute_knowledge(action="memify")
        elif arg == "stats":
            result = await execute_knowledge(action="stats")
        elif arg == "list":
            result = await execute_knowledge(action="list")
        elif arg == "prune":
            result = await execute_knowledge(action="prune")
        elif arg.startswith("delete "):
            ds = arg[7:].strip()
            result = await execute_knowledge(action="delete", dataset_name=ds)
        else:
            result = (
                "Usage: /knowledge <action> [args]\n"
                "Actions: add <text>, cognify, search <query>, memify, stats, list, delete <dataset>, prune"
            )
        await self._send_reply(update, result)

    # ── Run ──────────────────────────────────────────────────

    async def run(self) -> None:
        """Build and run the Telegram bot."""
        app = Application.builder().token(self.token).build()
        self._app = app

        # Register command handlers
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("usage", self.cmd_usage))
        app.add_handler(CommandHandler("session", self.cmd_session))
        app.add_handler(CommandHandler("new", self.cmd_new))
        app.add_handler(CommandHandler("clear", self.cmd_clear))
        app.add_handler(CommandHandler("compact", self.cmd_compact))
        app.add_handler(CommandHandler("model", self.cmd_model))
        app.add_handler(CommandHandler("provider", self.cmd_provider))
        app.add_handler(CommandHandler("perms", self.cmd_perms))
        app.add_handler(CommandHandler("memory", self.cmd_memory))
        app.add_handler(CommandHandler("skills", self.cmd_skills))
        app.add_handler(CommandHandler("crypt", self.cmd_crypt))
        app.add_handler(CommandHandler("abort", self.cmd_abort))
        app.add_handler(CommandHandler("plan", self.cmd_plan))
        app.add_handler(CommandHandler("go", self.cmd_go))
        app.add_handler(CommandHandler("init", self.cmd_init))
        app.add_handler(CommandHandler("new", self.cmd_new))
        app.add_handler(CommandHandler("get", self.cmd_get))
        app.add_handler(CommandHandler("getzip", self.cmd_getzip))
        app.add_handler(CommandHandler("ls", self.cmd_ls))
        app.add_handler(CommandHandler("run", self.cmd_run))
        app.add_handler(CommandHandler("files", self.cmd_files))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("restart", self.cmd_restart))

        # Assistant commands (always registered, only functional in assistant mode)
        app.add_handler(CommandHandler("task", self.cmd_task))
        app.add_handler(CommandHandler("tasks", self.cmd_tasks))
        app.add_handler(CommandHandler("taskdone", self.cmd_taskdone))
        app.add_handler(CommandHandler("note", self.cmd_note))
        app.add_handler(CommandHandler("notes", self.cmd_notes))
        app.add_handler(CommandHandler("remind", self.cmd_remind))
        app.add_handler(CommandHandler("reminders", self.cmd_reminders))
        app.add_handler(CommandHandler("briefing", self.cmd_briefing))
        app.add_handler(CommandHandler("config", self.cmd_config))
        app.add_handler(CommandHandler("knowledge", self.cmd_knowledge))

        # Message handlers
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text_message))
        app.add_handler(MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.VIDEO,
            self.on_file_message,
        ))

        logger.info("RedClaw Telegram bot starting...")
        await app.initialize()

        # Register bot commands for Telegram menu
        from telegram import BotCommand
        commands = [
            BotCommand("start", "Start / show welcome"),
            BotCommand("help", "Show all commands"),
            BotCommand("usage", "Token usage stats"),
            BotCommand("new", "New conversation session"),
            BotCommand("clear", "Clear conversation history"),
            BotCommand("compact", "Compact conversation context"),
            BotCommand("session", "Show session info"),
            BotCommand("model", "Get or set model"),
            BotCommand("provider", "Get or set provider"),
            BotCommand("perms", "Get or set permission mode"),
            BotCommand("memory", "Search memories"),
            BotCommand("skills", "List skills"),
            BotCommand("crypt", "Crypt wisdom stats"),
            BotCommand("abort", "Abort current response"),
            BotCommand("plan", "Enter plan mode (read-only)"),
            BotCommand("go", "Execute plan from .redclaw.md"),
            BotCommand("init", "Create .redclaw.md project context"),
            BotCommand("new", "Create project folder and switch"),
            BotCommand("get", "Download a file"),
            BotCommand("getzip", "Download folder as zip"),
            BotCommand("ls", "List working directory"),
            BotCommand("run", "Run a shell command"),
            BotCommand("files", "Search files by pattern"),
            BotCommand("status", "Bot status & uptime"),
            BotCommand("restart", "Restart the bot"),
            BotCommand("task", "Create a task"),
            BotCommand("tasks", "List tasks"),
            BotCommand("taskdone", "Mark task done"),
            BotCommand("note", "Create a note"),
            BotCommand("notes", "List notes"),
            BotCommand("remind", "Set a reminder"),
            BotCommand("reminders", "List reminders"),
            BotCommand("briefing", "Get daily briefing"),
            BotCommand("config", "Assistant config"),
            BotCommand("knowledge", "Knowledge graph search"),
        ]
        try:
            await app.bot.set_my_commands(commands)
        except Exception as e:
            logger.warning("Failed to set bot commands: %s", e)

        await app.start()
        await app.updater.start_polling()

        # Keep running until interrupted
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            # Stop schedulers
            for scheduler in self._schedulers.values():
                scheduler.stop()
            self._schedulers.clear()
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            # Close all sessions
            for s in self.sessions.values():
                await s.close()
