"""Telegram-бот — второй клиент сервиса, живёт в том же процессе, что и веб.

Обновления приходят на webhook ``/tg/webhook`` (прод, HTTPS) или забираются long
polling'ом фоновой задачей (локально). Сценарии — общие с вебом, из ``app.service``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.types import BotCommand, Update

from app.bot.handlers import make_router

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/tg/webhook"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
ALLOWED_UPDATES = ["message", "callback_query"]
COMMANDS = [
    BotCommand(command="spin", description="Новая фраза"),
    BotCommand(command="help", description="Что умеют кнопки"),
]


class TelegramBot:
    def __init__(
        self, app_state: Any, token: str, secret: str, session: BaseSession | None = None
    ) -> None:
        self.secret = secret
        self.bot = Bot(
            token,
            session=session,
            default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True),
        )
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(make_router())
        self.dispatcher["app_state"] = app_state
        self.dispatcher["runtime"] = self
        self.card_ids: dict[str, str] = {}  # slug → file_id открытки, уже загруженной в Telegram
        self._polling: asyncio.Task | None = None

    async def start(self, webhook_url: str | None) -> None:
        """Регистрирует команды и webhook; без ``webhook_url`` — запускает polling.

        Сбой связи с Telegram не валит веб: бот просто не заработает до рестарта.
        """
        try:
            await self.bot.set_my_commands(COMMANDS)
            if webhook_url:
                await self.bot.set_webhook(
                    webhook_url, secret_token=self.secret, allowed_updates=ALLOWED_UPDATES
                )
                log.info("Telegram: webhook %s", webhook_url)
            else:
                await self.bot.delete_webhook()
                self._polling = asyncio.create_task(
                    self.dispatcher.start_polling(
                        self.bot,
                        handle_signals=False,
                        close_bot_session=False,
                        allowed_updates=ALLOWED_UPDATES,
                    )
                )
                log.info("Telegram: long polling")
        except Exception:
            log.exception("Telegram-бот не запустился")

    async def stop(self) -> None:
        # Webhook не снимаем: при деплое новый экземпляр ставит его сам.
        if self._polling:
            with contextlib.suppress(RuntimeError):
                await self.dispatcher.stop_polling()
            with contextlib.suppress(asyncio.CancelledError):
                await self._polling
        await self.bot.session.close()

    async def feed(self, data: dict) -> None:
        """Обработка одного обновления из webhook. Ошибки — в лог, Telegram отвечаем 200."""
        update = Update.model_validate(data, context={"bot": self.bot})
        try:
            await self.dispatcher.feed_update(self.bot, update)
        except Exception:
            log.exception("ошибка обработки обновления Telegram %s", update.update_id)
