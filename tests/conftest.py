from pathlib import Path

import pytest

from app.lexicon import Lexicon, Word

LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "lexicon.jsonl"


@pytest.fixture(scope="session")
def lexicon() -> Lexicon:
    return Lexicon.from_jsonl(LEXICON_PATH)


@pytest.fixture(scope="session")
def word(lexicon):
    """Поиск слова словаря по лемме и категории."""

    def find(lemma: str, category: str, governs: str | None = None) -> Word:
        for item in lexicon.pool(category):
            if item.lemma == lemma and (governs is None or item.governs == governs):
                return item
        raise LookupError(f"{lemma} ({category}) нет в словаре")

    return find
