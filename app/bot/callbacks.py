"""Данные кнопок. Всё состояние фразы — в кнопке: slug и маска закреплённых слотов.

По slug база восстанавливает пресет и слоты, так что кнопки старых сообщений
работают и после перезапуска. Telegram ограничивает ``callback_data`` 64 байтами.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from aiogram.filters.callback_data import CallbackData

View = Literal["m", "w", "p", "t"]  # главный, слова, пресеты, тон


def to_mask(indices: Iterable[int]) -> int:
    mask = 0
    for index in indices:
        mask |= 1 << index
    return mask


def from_mask(mask: int) -> list[int]:
    return [index for index in range(mask.bit_length()) if mask >> index & 1]


class Spin(CallbackData, prefix="g"):
    slug: str
    mask: int


class Reroll(CallbackData, prefix="r"):
    slug: str
    mask: int
    i: int


class Lock(CallbackData, prefix="l"):
    slug: str
    mask: int
    i: int


class Show(CallbackData, prefix="v"):
    slug: str
    mask: int
    view: View


class ChoosePreset(CallbackData, prefix="p"):
    slug: str
    preset: str


class ChooseTone(CallbackData, prefix="t"):
    slug: str
    mask: int
    tone: str


class ToggleWild(CallbackData, prefix="x"):
    slug: str
    mask: int


class Like(CallbackData, prefix="k"):
    slug: str
    mask: int
    view: Literal["m", "c"]  # на главном экране или на свёрнутом сообщении


class Prompt(CallbackData, prefix="c"):
    slug: str
    kind: str


class Card(CallbackData, prefix="o"):
    slug: str


class Base(CallbackData, prefix="b"):
    """Взять фразу за основу: новое сообщение со всеми закреплёнными словами."""

    slug: str
