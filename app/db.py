"""SQLite: схема, загрузка словаря, фразы, сохранённые идеи и лайки."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.lexicon import Word

SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY,
    lemma TEXT NOT NULL,
    category TEXT NOT NULL,
    tier TEXT NOT NULL,
    gender TEXT,
    animacy TEXT,
    aspect TEXT,
    transitive INTEGER NOT NULL DEFAULT 0,
    governs TEXT,
    governs_prep TEXT,
    prep TEXT,
    prefer_loct INTEGER NOT NULL DEFAULT 0,
    qual INTEGER NOT NULL DEFAULT 0,
    feel INTEGER NOT NULL DEFAULT 0,
    noactor INTEGER NOT NULL DEFAULT 0,
    noobject INTEGER NOT NULL DEFAULT 0,
    tones TEXT NOT NULL DEFAULT '',
    zipf REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS words_category ON words (category, tier);

CREATE TABLE IF NOT EXISTS phrases (
    slug TEXT PRIMARY KEY,
    preset_id TEXT NOT NULL,
    key_json TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_ideas (
    slug TEXT PRIMARY KEY REFERENCES phrases (slug),
    likes INTEGER NOT NULL DEFAULT 0,
    saved_at REAL NOT NULL,
    last_liked_at REAL
);
CREATE INDEX IF NOT EXISTS saved_ideas_saved_at ON saved_ideas (saved_at DESC, slug);

CREATE TABLE IF NOT EXISTS likes (
    slug TEXT NOT NULL REFERENCES phrases (slug),
    voter_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (slug, voter_hash)
);

-- Пользователи Telegram-бота: настройки и последнее сообщение с живой клавиатурой.
-- Ключ — тот же хэш, что голосует; tg_id (добавлен позже) — только для рассылки.
CREATE TABLE IF NOT EXISTS tg_users (
    user_hash TEXT PRIMARY KEY,
    preset_id TEXT,
    tone TEXT NOT NULL DEFAULT 'auto',
    wild INTEGER NOT NULL DEFAULT 0,
    recent TEXT NOT NULL DEFAULT '[]',
    last_message_id INTEGER,
    last_slug TEXT,
    last_mask INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    seen_at REAL NOT NULL
);

-- Журнал действий для админки: без текста и личных данных, actor — префикс хэша голосующего.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    actor TEXT NOT NULL,
    preset_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_created_at ON events (created_at);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

WORD_COLUMNS = (
    "id", "lemma", "category", "tier", "gender", "animacy", "aspect", "transitive", "governs",
    "governs_prep", "prep", "prefer_loct", "qual", "feel", "noactor", "noobject", "tones", "zipf",
)  # fmt: skip

# «Зал славы»: затухание как у Hacker News — score = likes / (часы + 2) ^ GRAVITY.
GRAVITY = 1.5
TOP_CANDIDATES = 2000


@asynccontextmanager
async def connect(path: Path) -> AsyncIterator[aiosqlite.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db
    finally:
        await db.close()


# Столбцы, добавленные в существующие таблицы после их появления: CREATE TABLE IF NOT EXISTS
# их не создаст в старой базе.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    # Telegram ID — для будущей рассылки; ключ строки по-прежнему хэш.
    "tg_users": {"tg_id": "INTEGER"},
}


async def create_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            existing = {row[1] async for row in cur}
        for name, decl in columns.items():
            if name not in existing:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    await db.commit()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def load_lexicon(db: aiosqlite.Connection, path: Path) -> bool:
    """Идемпотентно загружает словарь: перезаливает ``words``, только если файл изменился.

    Таблицы ``phrases`` и ``saved_ideas`` не трогаются. Возвращает True, если словарь
    был перезагружен.
    """
    digest = file_hash(path)
    async with db.execute("SELECT value FROM meta WHERE key = 'lexicon_hash'") as cursor:
        row = await cursor.fetchone()
    if row is not None and row["value"] == digest:
        return False
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                record = Word.from_record(json.loads(line)).to_record()
                record["tones"] = ",".join(record["tones"])
                rows.append(tuple(record[column] for column in WORD_COLUMNS))
    placeholders = ", ".join("?" for _ in WORD_COLUMNS)
    await db.execute("DELETE FROM words")
    await db.executemany(
        f"INSERT INTO words ({', '.join(WORD_COLUMNS)}) VALUES ({placeholders})", rows
    )
    await db.execute(
        "INSERT INTO meta (key, value) VALUES ('lexicon_hash', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (digest,),
    )
    await db.commit()
    return True


async def read_words(db: aiosqlite.Connection) -> list[Word]:
    words = []
    async with db.execute(f"SELECT {', '.join(WORD_COLUMNS)} FROM words") as cursor:
        async for row in cursor:
            record = dict(row)
            record["tones"] = [t for t in record["tones"].split(",") if t]
            words.append(Word.from_record(record))
    return words


async def remember_phrase(
    db: aiosqlite.Connection, slug: str, preset_id: str, key: dict, text: str
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO phrases (slug, preset_id, key_json, text, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (slug, preset_id, json.dumps(key, ensure_ascii=False), text, time.time()),
    )
    await db.commit()


async def get_phrase(db: aiosqlite.Connection, slug: str) -> dict | None:
    async with db.execute(
        "SELECT p.slug, p.preset_id, p.key_json, p.text, p.created_at, "
        "s.likes, s.saved_at FROM phrases p LEFT JOIN saved_ideas s ON s.slug = p.slug "
        "WHERE p.slug = ?",
        (slug,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    phrase = dict(row)
    phrase["key"] = json.loads(phrase.pop("key_json"))
    phrase["likes"] = phrase["likes"] or 0
    return phrase


async def save_idea(db: aiosqlite.Connection, slug: str) -> int:
    """Отправляет фразу в «Зал славы». Возвращает текущее число лайков."""
    await db.execute(
        "INSERT OR IGNORE INTO saved_ideas (slug, likes, saved_at) VALUES (?, 0, ?)",
        (slug, time.time()),
    )
    await db.commit()
    async with db.execute("SELECT likes FROM saved_ideas WHERE slug = ?", (slug,)) as cursor:
        row = await cursor.fetchone()
    return row["likes"]


async def like(db: aiosqlite.Connection, slug: str, voter_hash: str) -> tuple[int, bool]:
    """Один голос на фразу. Возвращает (лайки, засчитан ли голос)."""
    await save_idea(db, slug)
    now = time.time()
    cursor = await db.execute(
        "INSERT OR IGNORE INTO likes (slug, voter_hash, created_at) VALUES (?, ?, ?)",
        (slug, voter_hash, now),
    )
    counted = cursor.rowcount == 1
    if counted:
        await db.execute(
            "UPDATE saved_ideas SET likes = likes + 1, last_liked_at = ? WHERE slug = ?",
            (now, slug),
        )
    await db.commit()
    async with db.execute("SELECT likes FROM saved_ideas WHERE slug = ?", (slug,)) as cur:
        row = await cur.fetchone()
    return row["likes"], counted


def hot_score(likes: int, saved_at: float, now: float) -> float:
    hours = max(now - saved_at, 0.0) / 3600
    return likes / math.pow(hours + 2, GRAVITY)


FEED_BASE = (
    "SELECT p.slug, p.preset_id, p.text, s.likes, s.saved_at "
    "FROM saved_ideas s JOIN phrases p ON p.slug = s.slug "
)


async def feed(
    db: aiosqlite.Connection, sort: str, cursor: str | None, limit: int
) -> tuple[list[dict], str | None]:
    """Лента: «top» — по лайкам с затуханием, «new» — по времени сохранения.

    Курсор непрозрачен для клиента: для «new» это «saved_at:slug», для «top» — смещение.
    """
    if sort == "new":
        params: list = []
        where = ""
        if cursor:
            saved_at, _, slug = cursor.partition(":")
            where = "WHERE (s.saved_at < ?) OR (s.saved_at = ? AND s.slug > ?) "
            params = [float(saved_at), float(saved_at), slug]
        sql = FEED_BASE + where + "ORDER BY s.saved_at DESC, s.slug ASC LIMIT ?"
        async with db.execute(sql, [*params, limit + 1]) as cur:
            rows = [dict(row) async for row in cur]
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            next_cursor = f"{last['saved_at']!r}:{last['slug']}"
        return rows, next_cursor

    offset = int(cursor) if cursor and cursor.isdigit() else 0
    rows = await _top_rows(db)
    page = rows[offset : offset + limit]
    next_cursor = str(offset + limit) if offset + limit < len(rows) else None
    return page, next_cursor


async def _top_rows(db: aiosqlite.Connection) -> list[dict]:
    """«Зал славы» целиком: фразы с лайками по убыванию hot_score."""
    sql = FEED_BASE + "WHERE s.likes > 0 ORDER BY s.last_liked_at DESC LIMIT ?"
    async with db.execute(sql, (TOP_CANDIDATES,)) as cur:
        rows = [dict(row) async for row in cur]
    now = time.time()
    rows.sort(key=lambda r: (-hot_score(r["likes"], r["saved_at"], now), r["slug"]))
    return rows


async def feed_at(db: aiosqlite.Connection, sort: str, offset: int) -> tuple[dict | None, int]:
    """Одна фраза ленты по смещению и длина ленты — для карусели в боте."""
    offset = max(offset, 0)
    if sort == "new":
        async with db.execute("SELECT COUNT(*) FROM saved_ideas") as cur:
            total = (await cur.fetchone())[0]
        sql = FEED_BASE + "ORDER BY s.saved_at DESC, s.slug ASC LIMIT 1 OFFSET ?"
        async with db.execute(sql, (offset,)) as cur:
            row = await cur.fetchone()
        return (dict(row) if row else None), total
    rows = await _top_rows(db)
    return (rows[offset] if offset < len(rows) else None), len(rows)


# --- Telegram ---------------------------------------------------------------------------------

TG_USER_FIELDS = (
    "preset_id", "tone", "wild", "recent", "last_message_id", "last_slug", "last_mask", "tg_id",
)  # fmt: skip


async def tg_user(db: aiosqlite.Connection, user_hash: str, tg_id: int | None = None) -> dict:
    """Настройки пользователя бота; для нового — значения по умолчанию (без записи).

    ``tg_id`` запоминается при следующем сохранении — для рассылки.
    """
    user = await _tg_user(db, user_hash)
    if tg_id is not None:
        user["tg_id"] = tg_id
    return user


async def _tg_user(db: aiosqlite.Connection, user_hash: str) -> dict:
    async with db.execute("SELECT * FROM tg_users WHERE user_hash = ?", (user_hash,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return {
            "user_hash": user_hash,
            "preset_id": None,
            "tone": "auto",
            "wild": False,
            "recent": [],
            "last_message_id": None,
            "last_slug": None,
            "last_mask": 0,
            "tg_id": None,
        }
    user = dict(row)
    user["wild"] = bool(user["wild"])
    user["recent"] = json.loads(user["recent"])
    return user


async def save_tg_user(db: aiosqlite.Connection, user: dict) -> None:
    now = time.time()
    values = [user[name] for name in TG_USER_FIELDS]
    values[TG_USER_FIELDS.index("wild")] = int(user["wild"])
    values[TG_USER_FIELDS.index("recent")] = json.dumps(user["recent"])
    columns = ", ".join(TG_USER_FIELDS)
    updates = ", ".join(f"{name} = excluded.{name}" for name in TG_USER_FIELDS)
    await db.execute(
        f"INSERT INTO tg_users (user_hash, {columns}, created_at, seen_at) "
        f"VALUES (?, {', '.join('?' * len(values))}, ?, ?) "
        f"ON CONFLICT (user_hash) DO UPDATE SET {updates}, seen_at = excluded.seen_at",
        (user["user_hash"], *values, now, now),
    )
    await db.commit()


async def stats(db: aiosqlite.Connection, now: float | None = None) -> dict:
    """Сводка для админа: фразы, зал славы, лайки, пользователи бота."""
    now = time.time() if now is None else now
    day, week = now - 86400, now - 7 * 86400

    async def one(sql: str, *params) -> int:
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
        return row[0] or 0

    async with db.execute(
        "SELECT p.text, s.likes FROM saved_ideas s JOIN phrases p ON p.slug = s.slug "
        "WHERE s.likes > 0 ORDER BY s.likes DESC, s.saved_at DESC LIMIT 3"
    ) as cur:
        top = [(row["text"], row["likes"]) async for row in cur]
    return {
        "phrases": await one("SELECT COUNT(*) FROM phrases"),
        "phrases_day": await one("SELECT COUNT(*) FROM phrases WHERE created_at >= ?", day),
        "saved": await one("SELECT COUNT(*) FROM saved_ideas"),
        "likes": await one("SELECT COUNT(*) FROM likes"),
        "likes_day": await one("SELECT COUNT(*) FROM likes WHERE created_at >= ?", day),
        "voters": await one("SELECT COUNT(DISTINCT voter_hash) FROM likes"),
        "tg_users": await one("SELECT COUNT(*) FROM tg_users"),
        "tg_day": await one("SELECT COUNT(*) FROM tg_users WHERE seen_at >= ?", day),
        "tg_week": await one("SELECT COUNT(*) FROM tg_users WHERE seen_at >= ?", week),
        "top": top,
    }


# --- события и отчёт для админки -------------------------------------------------------------

DAY = 86400


async def log_event(
    db: aiosqlite.Connection,
    kind: str,
    channel: str,
    actor: str,
    preset_id: str | None = None,
    now: float | None = None,
) -> None:
    await db.execute(
        "INSERT INTO events (kind, channel, actor, preset_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (kind, channel, actor, preset_id, time.time() if now is None else now),
    )
    await db.commit()


async def admin_report(db: aiosqlite.Connection, days: int, now: float | None = None) -> dict:
    """Ряды по дням (UTC) за ``days`` дней, включая сегодняшний, и сводки за период."""
    now = time.time() if now is None else now
    today = int(now // DAY) * DAY
    since = today - (days - 1) * DAY
    dates = [time.strftime("%Y-%m-%d", time.gmtime(since + i * DAY)) for i in range(days)]

    async def rows(sql: str, *params) -> list[dict]:
        async with db.execute(sql, params) as cur:
            return [dict(row) async for row in cur]

    daily = await rows(
        "SELECT date(created_at, 'unixepoch') AS day, kind, channel, COUNT(*) AS n "
        "FROM events WHERE created_at >= ? GROUP BY day, kind, channel",
        since,
    )
    active = await rows(
        "SELECT date(created_at, 'unixepoch') AS day, channel, COUNT(DISTINCT actor) AS n "
        "FROM events WHERE created_at >= ? GROUP BY day, channel",
        since,
    )
    totals = await rows(
        "SELECT kind, channel, COUNT(*) AS n FROM events WHERE created_at >= ? "
        "GROUP BY kind, channel",
        since,
    )
    actors = await rows(
        "SELECT channel, COUNT(DISTINCT actor) AS n FROM events WHERE created_at >= ? "
        "GROUP BY channel",
        since,
    )
    presets = await rows(
        "SELECT preset_id, COUNT(*) AS n FROM events WHERE created_at >= ? "
        "AND kind IN ('generate', 'reroll') AND preset_id IS NOT NULL "
        "GROUP BY preset_id ORDER BY n DESC",
        since,
    )
    top = await rows(
        "SELECT p.slug, p.preset_id, p.text, s.likes FROM saved_ideas s "
        "JOIN phrases p ON p.slug = s.slug WHERE s.likes > 0 "
        "ORDER BY s.likes DESC, s.saved_at DESC LIMIT 10"
    )

    def series(source: list[dict], **match) -> list[int]:
        by_day = {d: 0 for d in dates}
        for row in source:
            if row["day"] in by_day and all(row[k] in v for k, v in match.items()):
                by_day[row["day"]] += row["n"]
        return list(by_day.values())

    channels = ("web", "tg")
    generated = ({"generate", "reroll"},)
    return {
        "days": dates,
        "generated": {c: series(daily, kind=generated[0], channel={c}) for c in channels},
        "likes": {c: series(daily, kind={"like"}, channel={c}) for c in channels},
        "active": {c: series(active, channel={c}) for c in channels},
        "totals": {f"{row['kind']}:{row['channel']}": row["n"] for row in totals},
        "actors": {row["channel"]: row["n"] for row in actors},
        "presets": [(row["preset_id"], row["n"]) for row in presets],
        "top": top,
        "summary": await stats(db, now),
    }
