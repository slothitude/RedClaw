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
                 mcp_servers: list[str] | None = None):
        self.user_id = user_id
        self.working_dir = working_dir
        self.provider_name = provider_name
        self.model = model
        self.mcp_servers = mcp_servers or []

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

        self.rt = ConversationRuntime(
            client=self.client,
            provider=self.provider,
            model=model,
            session=self.session,
            tools=self.tools,
            permission_policy=self.policy,
            usage_tracker=self.tracker,
            working_dir=cwd,
        )
        self.current_task: asyncio.Task | None = None

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
    ):
        self.token = token
        self.allowed_user_id = allowed_user_id
        self.working_dir = working_dir or str(Path.cwd())
        self.provider_name = provider_name
        self.model = model
        self.base_url = base_url
        self.perm_mode = perm_mode
        self.search_url = search_url
        self.reader_url = reader_url
        self.mcp_servers = mcp_servers or []
        self.sessions: dict[int, TelegramSession] = {}

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
            )
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
        await self._send_reply(update, (
            "*RedClaw* — AI Coding Agent\n\n"
            "Send me a message and I'll process it with my coding tools.\n\n"
            "Commands:\n"
            "/help — List all commands\n"
            "/usage — Token usage stats\n"
            "/session — Current session info\n"
            "/new — New session\n"
            "/compact — Compact history\n"
            "/model [name] — Show/set model\n"
            "/abort — Abort current turn\n"
            "/get <path> — Download file\n"
            "/getzip <path> — Download directory as zip\n"
            "/ls [path] — List files\n"
            "/run <cmd> — Direct bash command\n"
            "/files — List uploaded files\n"
            "/status — Connection + agent status\n"
            "/restart — Restart bot (reloads MCP servers)"
        ))

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        await self._send_reply(update, (
            "RedClaw Commands:\n\n"
            "/start — Welcome + instructions\n"
            "/help — This help message\n"
            "/usage — Token usage stats\n"
            "/session — Current session info\n"
            "/new — Start a new session\n"
            "/compact — Compact conversation history\n"
            "/model [name] — Show or set model\n"
            "/abort — Abort current turn\n"
            "/get <path> — Download a file\n"
            "/getzip <path> — Download directory as zip\n"
            "/ls [path] — List files in directory\n"
            "/run <cmd> — Run a bash command (bypasses LLM)\n"
            "/files — List uploaded files\n"
            "/status — Connection and agent status\n\n"
            "Just type a message to chat with the agent.\n"
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

        # Check if already processing
        if s.current_task and not s.current_task.done():
            await self._send_reply(update, "Still processing previous message. Use /abort to cancel.")
            return

        # React with processing indicator
        try:
            await update.message.set_reaction("⚡")
        except Exception:
            pass

        async def _process() -> None:
            collected_text = ""
            tool_names: list[str] = []
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
                pass  # Don't spam tool results in chat

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

    # ── Run ──────────────────────────────────────────────────

    async def run(self) -> None:
        """Build and run the Telegram bot."""
        app = Application.builder().token(self.token).build()

        # Register command handlers
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("usage", self.cmd_usage))
        app.add_handler(CommandHandler("session", self.cmd_session))
        app.add_handler(CommandHandler("new", self.cmd_new))
        app.add_handler(CommandHandler("compact", self.cmd_compact))
        app.add_handler(CommandHandler("model", self.cmd_model))
        app.add_handler(CommandHandler("abort", self.cmd_abort))
        app.add_handler(CommandHandler("get", self.cmd_get))
        app.add_handler(CommandHandler("getzip", self.cmd_getzip))
        app.add_handler(CommandHandler("ls", self.cmd_ls))
        app.add_handler(CommandHandler("run", self.cmd_run))
        app.add_handler(CommandHandler("files", self.cmd_files))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("restart", self.cmd_restart))

        # Message handlers
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text_message))
        app.add_handler(MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.VIDEO,
            self.on_file_message,
        ))

        logger.info("RedClaw Telegram bot starting...")
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        # Keep running until interrupted
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            # Close all sessions
            for s in self.sessions.values():
                await s.close()
