"""Конфигурация из переменных окружения."""

from __future__ import annotations

import os
import secrets
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


def default_base_url() -> str:
    """``BASE_URL``, иначе публичный домен Railway, иначе локальный адрес."""
    if base_url := os.getenv("BASE_URL"):
        return base_url
    if domain := os.getenv("RAILWAY_PUBLIC_DOMAIN"):
        return f"https://{domain}"
    return "http://localhost:8000"


def load_or_create_salt(data_dir: Path) -> str:
    """Соль из ``data_dir``; при первом запуске генерируется и сохраняется туда же."""
    return load_or_create_secret(data_dir, "secret_salt")


def load_or_create_secret(data_dir: Path, name: str) -> str:
    """Секрет из файла ``data_dir/name``; при первом запуске генерируется и сохраняется."""
    path = data_dir / name
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    salt = secrets.token_hex(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(salt, encoding="utf-8")
    path.chmod(0o600)
    return salt


def parse_ids(value: str) -> frozenset[int]:
    """Список Telegram ID через запятую или пробел; мусор пропускается."""
    return frozenset(int(item) for item in value.replace(",", " ").split() if item.isdigit())


@dataclass(frozen=True)
class Settings:
    base_url: str = field(default_factory=lambda: default_base_url().rstrip("/"))
    # Пустая соль заменяется сохранённой в data_dir при старте приложения.
    secret_salt: str = field(default_factory=lambda: os.getenv("SECRET_SALT", ""))
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", str(ROOT_DIR / "var")))
    )
    lexicon_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("LEXICON_PATH", str(ROOT_DIR / "data" / "lexicon.jsonl"))
        )
    )
    # Telegram-бот: без токена выключен.
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    # Пустой секрет заменяется сохранённым в data_dir при старте бота.
    telegram_webhook_secret: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    )
    # webhook / polling; пусто — webhook, если base_url на HTTPS.
    telegram_mode: str = field(default_factory=lambda: os.getenv("TELEGRAM_MODE", ""))
    telegram_admin_ids: frozenset[int] = field(
        default_factory=lambda: parse_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))
    )

    @property
    def telegram_webhook(self) -> bool:
        if self.telegram_mode in ("webhook", "polling"):
            return self.telegram_mode == "webhook"
        return self.base_url.startswith("https://")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "storyteller.sqlite3"

    @property
    def og_cache_dir(self) -> Path:
        return self.data_dir / "cache" / "og"


def get_settings() -> Settings:
    return Settings()
