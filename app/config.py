"""Конфигурация из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

PROMPT_TEMPLATES: dict[str, str] = {
    "story": (
        "Напиши завязку рассказа на 300 слов по сюжету: «{phrase}» "
        "Сохрани интонацию фразы, дай герою имя и одну яркую деталь."
    ),
    "art": (
        "Опиши сцену для иллюстрации по сюжету: «{phrase}» "
        "Укажи композицию, атмосферу, время суток, источник и цвет света, палитру."
    ),
}


@dataclass(frozen=True)
class Settings:
    base_url: str = field(
        default_factory=lambda: os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    )
    secret_salt: str = field(default_factory=lambda: os.getenv("SECRET_SALT", "dev-salt"))
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", str(ROOT_DIR / "var")))
    )
    lexicon_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("LEXICON_PATH", str(ROOT_DIR / "data" / "lexicon.jsonl"))
        )
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "storyteller.sqlite3"

    @property
    def og_cache_dir(self) -> Path:
        return self.data_dir / "cache" / "og"


def get_settings() -> Settings:
    return Settings()
