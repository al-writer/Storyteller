"""Telegram-бот — второй клиент сервиса, живёт в том же процессе, что и веб.

Обновления приходят только на webhook ``/tg/webhook``: бот один, боевой, и работает
там, где есть публичный HTTPS. Сценарии — общие с вебом, из ``app.service``.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.types import BotCommand, BotCommandScopeChat, Update

from app.bot.handlers import make_router

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/tg/webhook"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
ALLOWED_UPDATES = ["message", "callback_query", "inline_query"]
COMMANDS = [
    BotCommand(command="spin", description="Новая фраза"),
    BotCommand(command="top", description="Зал славы"),
    BotCommand(command="new", description="Прямой эфир: только что сохранённые"),
    BotCommand(command="help", description="Что умеют кнопки"),
]
ADMIN_COMMANDS = [*COMMANDS, BotCommand(command="stats", description="Статистика")]


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
        self.app_state = app_state
        self.dispatcher["app_state"] = app_state
        self.dispatcher["runtime"] = self
        self.card_ids: dict[str, str] = {}  # slug → file_id открытки, уже загруженной в Telegram

    async def start(self, webhook_url: str) -> None:
        """Регистрирует команды и webhook.

        Сбой связи с Telegram не валит веб: бот просто не заработает до рестарта.
        """
        try:
            await self.bot.set_my_commands(COMMANDS)
            for admin_id in self.app_state.settings.telegram_admin_ids:
                # Чат админа может быть ещё не открыт — тогда меню без /stats, не беда.
                with contextlib.suppress(Exception):
                    await self.bot.set_my_commands(
                        ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
                    )
            await self.bot.set_webhook(
                webhook_url, secret_token=self.secret, allowed_updates=ALLOWED_UPDATES
            )
            log.info("Telegram: webhook %s", webhook_url)
        except Exception:
            log.exception("Telegram-бот не запустился")

    async def stop(self) -> None:
        # Webhook не снимаем: при деплое новый экземпляр ставит его сам.
        await self.bot.session.close()

    async def feed(self, data: dict) -> None:
        """Обработка одного обновления из webhook. Ошибки — в лог, Telegram отвечаем 200."""
        update = Update.model_validate(data, context={"bot": self.bot})
        try:
            await self.dispatcher.feed_update(self.bot, update)
        except Exception:
            log.exception("ошибка обработки обновления Telegram %s", update.update_id)
