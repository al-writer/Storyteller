"""Сценарии сервиса, общие для всех клиентов: веба и Telegram-бота.

Функции принимают ``state`` приложения (``app.state``) и читают из него настройки,
базу, движок и лимитеры в момент вызова. Ошибки — подклассы ``ServiceError``; каждый
клиент сам решает, как их показать.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool

from app import db, og
from app.config import PROMPT_TEMPLATES, Settings
from app.engine import Engine, GenerationError, Phrase, RenderError, Slot, render, slots_from_key
from app.presets import PRESETS
from app.schemas import (
    FeedItem,
    FeedOut,
    GenerateIn,
    LikeOut,
    PhraseOut,
    SaveOut,
    SlotOut,
    TokenOut,
)

log = logging.getLogger(__name__)

CardFormat = Literal["wide", "square"]


# --- ошибки ---------------------------------------------------------------------------------


class ServiceError(Exception):
    """Ожидаемый отказ сценария; текст — для пользователя."""

    message = "ошибка"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)


class TooFast(ServiceError):
    message = "слишком часто"


class NotFound(ServiceError):
    message = "фраза не найдена"


class Gone(ServiceError):
    message = "слова этой фразы больше нет в словаре"


class Invalid(ServiceError):
    message = "неверный запрос"


class Failed(ServiceError):
    message = "не удалось собрать фразу"


# --- клиент ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Client:
    """Кто делает запрос: ключ для лимитов и хэш для голосования."""

    key: str
    voter_hash: str

    @classmethod
    def from_source(cls, salt: str, source: str) -> Client:
        """``source`` — стабильный идентификатор клиента: ``cookie:…``, ``ip:…``, ``tg:…``."""
        digest = hashlib.sha256(f"{salt}|{source}".encode()).hexdigest()
        return cls(key=digest[:32], voter_hash=digest)


def valid_slug(slug: str) -> bool:
    return slug.isalnum() and len(slug) <= 16


# --- сериализация ---------------------------------------------------------------------------


def phrase_url(settings: Settings, slug: str) -> str:
    return f"{settings.base_url}/s/{slug}"


def phrase_out(settings: Settings, phrase: Phrase, likes: int = 0) -> PhraseOut:
    slot_texts: dict[int, list[str]] = {}
    for token in phrase.tokens:
        if token.slot is not None:
            slot_texts.setdefault(token.slot, []).append(token.text)
    slots = [
        SlotOut(
            index=index,
            name=spec.name,
            category=spec.category,
            id=slot.id,
            lemma=word.lemma,
            text=" ".join(slot_texts.get(index, [])),
            number=slot.number,
            gender=slot.gender,
            superlative=slot.superlative,
        )
        for index, (spec, slot, word) in enumerate(
            zip(phrase.preset.slots, phrase.slots, phrase.words, strict=True)
        )
    ]
    return PhraseOut(
        slug=phrase.slug,
        url=phrase_url(settings, phrase.slug),
        preset_id=phrase.preset.id,
        preset_title=phrase.preset.title,
        text=phrase.text,
        tone=phrase.tone,
        card_tone=phrase.card_tone,
        slots=slots,
        tokens=[TokenOut(text=t.text, slot=t.slot) for t in phrase.tokens],
        prompt={
            name: template.format(phrase=phrase.text) for name, template in PROMPT_TEMPLATES.items()
        },
        likes=likes,
    )


# --- сценарии -------------------------------------------------------------------------------


async def generate(state: Any, who: Client, params: GenerateIn) -> PhraseOut:
    if not state.generate_limiter.allow(who.key):
        raise TooFast
    if params.preset_id not in PRESETS:
        raise Invalid("неизвестный пресет")
    engine: Engine = state.engine
    try:
        phrase = await run_in_threadpool(
            engine.generate,
            params.preset_id,
            current=[Slot(**slot.model_dump()) for slot in params.slots],
            locked=params.locked_indices,
            reroll=params.reroll_index,
            recent_ids=params.recent_ids,
            tone=None if params.tone in (None, "auto") else params.tone,
            wild=params.wild,
            seed=params.seed,
        )
    except GenerationError as error:
        log.error("генерация не удалась: %s", error)
        raise Failed from error
    await db.remember_phrase(state.db, phrase.slug, phrase.preset.id, phrase.key(), phrase.text)
    return phrase_out(state.settings, phrase)


async def load(state: Any, slug: str) -> tuple[dict, Phrase | None]:
    """Фраза из базы и, если слова ещё есть в словаре, её пересборка из ключа."""
    row = await db.get_phrase(state.db, slug)
    if row is None:
        raise NotFound
    preset_id, slots = slots_from_key(row["key"])
    preset = PRESETS.get(preset_id)
    try:
        phrase = render(state.lexicon, preset, slots) if preset else None
    except (RenderError, GenerationError):
        phrase = None
    return row, phrase


async def get_phrase(state: Any, slug: str) -> PhraseOut:
    row, phrase = await load(state, slug)
    if phrase is None:
        raise Gone
    return phrase_out(state.settings, phrase, likes=row["likes"])


async def save(state: Any, slug: str) -> SaveOut:
    if await db.get_phrase(state.db, slug) is None:
        raise NotFound
    likes = await db.save_idea(state.db, slug)
    return SaveOut(id=slug, slug=slug, likes=likes)


async def like(state: Any, who: Client, slug: str) -> LikeOut:
    if not state.like_limiter.allow(who.key):
        raise TooFast
    if await db.get_phrase(state.db, slug) is None:
        raise NotFound
    likes, counted = await db.like(state.db, slug, who.voter_hash)
    return LikeOut(likes=likes, counted=counted)


async def feed(state: Any, sort: Literal["top", "new"], cursor: str | None, limit: int) -> FeedOut:
    rows, next_cursor = await db.feed(state.db, sort, cursor, limit)
    items = [feed_item(state.settings, row) for row in rows]
    return FeedOut(items=items, next_cursor=next_cursor)


async def feed_at(
    state: Any, sort: Literal["top", "new"], offset: int
) -> tuple[FeedItem | None, int]:
    """Одна фраза ленты по смещению и длина ленты."""
    row, total = await db.feed_at(state.db, sort, offset)
    return (feed_item(state.settings, row) if row else None), total


def feed_item(settings: Settings, row: dict) -> FeedItem:
    return FeedItem(
        slug=row["slug"],
        url=phrase_url(settings, row["slug"]),
        text=row["text"],
        likes=row["likes"],
        preset_id=row["preset_id"],
        preset_title=PRESETS[row["preset_id"]].title if row["preset_id"] in PRESETS else "",
        saved_at=row["saved_at"],
    )


async def card_path(state: Any, slug: str, format: CardFormat = "wide") -> Path:
    """PNG-открытка в кэше; рисуется при первом запросе.

    На открытке напечатан домен, так что кэш раскладывается по папкам доменов: после
    смены ``BASE_URL`` открытки рисуются заново.
    """
    if not valid_slug(slug):
        raise NotFound
    settings: Settings = state.settings
    host = urlsplit(settings.base_url).netloc or settings.base_url
    path = settings.og_cache_dir / re.sub(r"[^A-Za-z0-9.-]", "_", host) / f"{slug}_{format}.png"
    if not path.exists():
        row, phrase = await load(state, slug)
        preset = PRESETS.get(row["preset_id"])
        png = await run_in_threadpool(
            og.render_card,
            row["text"],
            preset.title if preset else "",
            phrase.card_tone if phrase else None,
            format,
            host,
            slug,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(png)
        tmp.replace(path)
    return path
