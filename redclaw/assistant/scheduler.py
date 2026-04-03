"""Background scheduler for reminders and daily briefings."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from redclaw.assistant.config import AssistantConfig
from redclaw.assistant.reminders import ReminderStore
from redclaw.assistant.tasks import TaskStore
from redclaw.assistant.briefing import BriefingGenerator

logger = logging.getLogger(__name__)

# Callback type: (user_id, text) -> None
SendFn = Callable[[int, str], Awaitable[None]]


class AssistantScheduler:
    """Background asyncio loop that checks reminders and triggers briefings."""

    def __init__(
        self,
        config: AssistantConfig,
        tasks: TaskStore,
        reminders: ReminderStore,
        send_fn: SendFn,
        user_id: int,
        search_url: str | None = None,
    ) -> None:
        self.config = config
        self.tasks = tasks
        self.reminders = reminders
        self.send_fn = send_fn
        self.user_id = user_id
        self.search_url = search_url
        self._task: asyncio.Task | None = None
        self._last_briefing_date: str = ""  # track to avoid duplicate briefings

    def start(self) -> None:
        """Start the background scheduler loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("Assistant scheduler started")

    def stop(self) -> None:
        """Stop the background scheduler."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Assistant scheduler stopped")

    async def _loop(self) -> None:
        """Main loop — tick every 30 seconds."""
        try:
            while True:
                await self._tick()
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

    async def _tick(self) -> None:
        """Single tick — check reminders and briefings."""
        now = datetime.now(timezone.utc)
        await self._check_reminders(now)
        await self._check_briefing(now)

    async def _check_reminders(self, now: datetime) -> None:
        """Find due reminders, push via callback, handle recurrence."""
        due = self.reminders.get_due(now)
        for reminder in due:
            try:
                await self.send_fn(self.user_id, f"Reminder: {reminder.text}")
                self.reminders.mark_delivered(reminder.id)
            except Exception as e:
                logger.error(f"Failed to send reminder {reminder.id}: {e}")

    async def _check_briefing(self, now: datetime) -> None:
        """Check if it's briefing time and send if needed."""
        if not self.config.briefing_enabled:
            return

        try:
            tz = ZoneInfo(self.config.timezone)
        except Exception:
            tz = ZoneInfo("UTC")

        local_now = now.astimezone(tz)
        today_str = local_now.strftime("%Y-%m-%d")

        # Only send once per day
        if self._last_briefing_date == today_str:
            return

        # Check if current time matches briefing time (within 1-minute window)
        briefing_h, briefing_m = self.config.briefing_time.split(":")
        if local_now.hour == int(briefing_h) and local_now.minute == int(briefing_m):
            self._last_briefing_date = today_str
            try:
                generator = BriefingGenerator(
                    config=self.config,
                    tasks=self.tasks,
                    reminders=self.reminders,
                    search_url=self.search_url,
                )
                briefing = await generator.generate()
                await self.send_fn(self.user_id, briefing)
            except Exception as e:
                logger.error(f"Failed to generate briefing: {e}")
