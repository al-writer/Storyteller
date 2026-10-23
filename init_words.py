"""Создать таблицы и загрузить data/lexicon.jsonl в SQLite (идемпотентно, по хэшу файла).

То же самое приложение делает при старте; скрипт нужен для ручной подготовки базы.
"""

from __future__ import annotations

import asyncio

from app import db
from app.config import get_settings


async def init_words() -> bool:
    settings = get_settings()
    async with db.connect(settings.db_path) as conn:
        await db.create_schema(conn)
        return await db.load_lexicon(conn, settings.lexicon_path)


if __name__ == "__main__":
    reloaded = asyncio.run(init_words())
    print("словарь загружен" if reloaded else "словарь не изменился")
