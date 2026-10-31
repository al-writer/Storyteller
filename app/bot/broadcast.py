"""Рассылка пользователям бота: фоновая задача с лимитом скорости и учётом блокировок."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

from app import db

log = logging.getLogger(__name__)

RATE = 25  # сообщений в секунду: у Telegram предел около 30 для рассылок
FLUSH_EVERY = 20  # как часто сохранять счётчики в базу
MAX_RETRIES = 3


class Busy(Exception):
    """Рассылка уже идёт."""


class Broadcaster:
    def __init__(self, bot: Bot, app_state: Any) -> None:
        self.bot = bot
        self.app_state = app_state
        self.task: asyncio.Task | None = None
        self.current: int | None = None
        self._cancel = False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def send(self, chat_id: int, text: str) -> None:
        """Одно сообщение простым текстом; разметку админ не верстает."""
        await self.bot.send_message(chat_id, text, parse_mode=None)

    async def start(self, text: str) -> int:
        if self.running:
            raise Busy
        recipients = await db.broadcast_recipients(self.app_state.db)
        broadcast_id = await db.create_broadcast(self.app_state.db, text, len(recipients))
        self.current, self._cancel = broadcast_id, False
        self.task = asyncio.create_task(self._run(broadcast_id, text, recipients))
        return broadcast_id

    def cancel(self, broadcast_id: int) -> bool:
        if not self.running or self.current != broadcast_id:
            return False
        self._cancel = True
        return True

    async def wait(self) -> None:
        if self.task:
            await self.task

    async def stop(self) -> None:
        """При остановке сервиса: задача отменяется, при старте её пометят прерванной."""
        if self.running:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def _run(self, broadcast_id: int, text: str, recipients: list[int]) -> None:
        counts = {"sent": 0, "failed": 0, "blocked": 0}
        conn = self.app_state.db
        status = "done"
        try:
            for index, chat_id in enumerate(recipients, 1):
                if self._cancel:
                    status = "cancelled"
                    break
                counts[await self._deliver(chat_id, text)] += 1
                if index % FLUSH_EVERY == 0:
                    await db.update_broadcast(conn, broadcast_id, **counts)
                await asyncio.sleep(1 / RATE)
        except Exception:
            log.exception("рассылка %s оборвалась", broadcast_id)
            status = "interrupted"
            raise
        finally:
            with contextlib.suppress(Exception):
                await db.update_broadcast(
                    conn, broadcast_id, status=status, finished_at=time.time(), **counts
                )

    async def _deliver(self, chat_id: int, text: str) -> str:
        for _ in range(MAX_RETRIES):
            try:
                await self.send(chat_id, text)
                return "sent"
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after)
            except TelegramForbiddenError:
                await db.mark_blocked(self.app_state.db, chat_id)
                return "blocked"
            except TelegramAPIError as error:
                log.warning("рассылка: %s — %s", chat_id, error)
                return "failed"
        return "failed"
