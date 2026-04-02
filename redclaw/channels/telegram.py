"""Telegram channel implementation."""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from redclaw.channels.base import ChannelBase, ChannelConfig, ChannelMessage

logger = logging.getLogger(__name__)

MAX_MSG_LEN = 4096


def _split_message(text: str) -> list[str]:
    if len(text) <= MAX_MSG_LEN:
        return [text]
    chunks = []
    while text:
        if len(text) <= MAX_MSG_LEN:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, MAX_MSG_LEN)
        if split_at < MAX_MSG_LEN // 2:
            split_at = MAX_MSG_LEN
        chunks.append(text[:split_at])
        text = text[split_at:]
    return chunks


class TelegramChannel(ChannelBase):
    """Telegram channel using python-telegram-bot."""

    def __init__(self, config: ChannelConfig, token: str, allowed_user_id: int | None = None) -> None:
        super().__init__(config)
        self.token = token
        self.allowed_user_id = allowed_user_id
        self._app: Application | None = None

    async def send_text(self, chat_id: str, text: str) -> None:
        if not self._app:
            return
        for chunk in _split_message(text):
            try:
                await self._app.bot.send_message(chat_id=int(chat_id), text=chunk)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")

    async def send_file(self, chat_id: str, file_path: str, filename: str | None = None) -> None:
        if not self._app:
            return
        path = Path(file_path)
        try:
            await self._app.bot.send_document(
                chat_id=int(chat_id),
                document=open(path, "rb"),
                filename=filename or path.name,
            )
        except Exception as e:
            logger.error(f"Failed to send file: {e}")

    async def send_typing(self, chat_id: str) -> None:
        if not self._app:
            return
        try:
            await self._app.bot.send_chat_action(chat_id=int(chat_id), action="typing")
        except Exception:
            pass

    async def start(self) -> None:
        self._app = Application.builder().token(self.token).build()

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("help", self._on_help))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self._app.add_handler(MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.VIDEO,
            self._on_file,
        ))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram channel started")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    def _check_user(self, update: Update) -> bool:
        if self.allowed_user_id is None:
            return True
        return update.effective_user.id == self.allowed_user_id

    async def _on_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        await self.send_text(str(update.effective_chat.id), (
            "RedClaw AI Coding Agent\n\n"
            "Send a message to chat with the agent.\n"
            "/help for commands."
        ))

    async def _on_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        await self.send_text(str(update.effective_chat.id), (
            "Commands: /start /help /usage /session /new /compact "
            "/model /abort /get /getzip /ls /run /files /status"
        ))

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        if not self._on_message:
            return
        msg = ChannelMessage(
            text=update.message.text or "",
            user_id=str(update.effective_user.id),
            chat_id=str(update.effective_chat.id),
            raw=update,
        )
        await self._on_message(msg)

    async def _on_file(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._check_user(update):
            return
        if not self._on_file:
            return

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
            file_obj = photo[-1]
            filename = f"photo_{update.message.message_id}.jpg"
        elif voice:
            file_obj = voice
            filename = f"voice_{update.message.message_id}.ogg"
        elif audio:
            file_obj = audio
            filename = audio.file_name or f"audio_{update.message.message_id}"
        elif video:
            file_obj = video
            filename = video.file_name or f"video_{update.message.message_id}.mp4"

        if not file_obj:
            return

        upload_dir = Path(self.config.working_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / filename

        try:
            tg_file = await file_obj.get_file()
            await tg_file.download_to_drive(str(dest))
        except Exception as e:
            logger.error(f"Failed to download file: {e}")
            return

        msg = ChannelMessage(
            text=f"File uploaded: {filename}",
            user_id=str(update.effective_user.id),
            chat_id=str(update.effective_chat.id),
            file_path=str(dest),
            file_name=filename,
            raw=update,
        )
        await self._on_file(msg)
