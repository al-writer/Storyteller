"""Небольшой типограф для открыток и страниц: неразрывные пробелы и тире."""

from __future__ import annotations

import re

NBSP = " "
# Короткие служебные слова, которые не должны оставаться в конце строки.
SHORT_WORDS = (
    "а", "в", "во", "и", "к", "ко", "о", "об", "обо", "с", "со", "у", "на", "но", "не", "ни",
    "по", "из", "за", "до", "от", "под", "над", "без", "для", "при", "про",
)  # fmt: skip

_SHORT = re.compile(
    r"(?<![\w-])(" + "|".join(sorted(SHORT_WORDS, key=len, reverse=True)) + r") ", re.IGNORECASE
)
_HYPHEN_AS_DASH = re.compile(r" [-–] ")
_DASH = re.compile(r" — ")


def typograph(text: str) -> str:
    text = _HYPHEN_AS_DASH.sub(" — ", text)
    text = _DASH.sub(f"{NBSP}— ", text)
    # Два прохода: короткие слова могут идти подряд («и с тех пор»).
    for _ in range(2):
        text = _SHORT.sub(lambda m: m.group(1) + NBSP, text)
    return text
