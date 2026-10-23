"""Модели запросов и ответов API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.presets import DEFAULT_PRESET

ToneParam = Literal["auto", "dark", "fairy", "cyber", "city", "myth"]


class SlotIn(BaseModel):
    id: int
    number: Literal["sing", "plur"] = "sing"
    gender: Literal["masc", "femn"] | None = None
    superlative: bool = False


class GenerateIn(BaseModel):
    """Сервер не принимает текст фразы — только ID слов и параметры формы."""

    preset_id: str = DEFAULT_PRESET
    slots: list[SlotIn] = Field(default_factory=list, max_length=16)
    locked_indices: list[int] = Field(default_factory=list, max_length=16)
    reroll_index: int | None = None
    recent_ids: list[int] = Field(default_factory=list, max_length=200)
    tone: ToneParam | None = None
    wild: bool = False
    seed: int | None = None


class SlotOut(BaseModel):
    index: int
    name: str
    category: str
    id: int
    lemma: str
    text: str
    number: str
    gender: str | None
    superlative: bool


class TokenOut(BaseModel):
    text: str
    slot: int | None


class PhraseOut(BaseModel):
    slug: str
    url: str
    preset_id: str
    preset_title: str
    text: str
    tone: str | None
    card_tone: str | None
    slots: list[SlotOut]
    tokens: list[TokenOut]
    prompt: dict[str, str]
    likes: int = 0


class SaveIn(BaseModel):
    slug: str = Field(min_length=1, max_length=16, pattern=r"^[0-9A-Za-z]+$")


class SaveOut(BaseModel):
    id: str
    slug: str
    likes: int


class LikeOut(BaseModel):
    likes: int
    counted: bool


class FeedItem(BaseModel):
    slug: str
    url: str
    text: str
    likes: int
    preset_id: str
    preset_title: str
    saved_at: float


class FeedOut(BaseModel):
    items: list[FeedItem]
    next_cursor: str | None
