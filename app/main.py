"""FastAPI-приложение: роуты и жизненный цикл."""

from __future__ import annotations

import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app import __version__, db, og
from app.config import PROMPT_TEMPLATES, ROOT_DIR, Settings, get_settings
from app.engine import Engine, GenerationError, Phrase, RenderError, Slot, render, slots_from_key
from app.lexicon import Lexicon, UsageTracker
from app.presets import PRESETS
from app.ratelimit import RateLimiter
from app.schemas import (
    FeedItem,
    FeedOut,
    GenerateIn,
    LikeOut,
    PhraseOut,
    SaveIn,
    SaveOut,
    SlotOut,
    TokenOut,
)
from app.typo import typograph

log = logging.getLogger(__name__)

VOTER_COOKIE = "st_vid"
VOTER_COOKIE_AGE = 60 * 60 * 24 * 365 * 2


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    async with db.connect(settings.db_path) as conn:
        await db.create_schema(conn)
        if await db.load_lexicon(conn, settings.lexicon_path):
            log.info("словарь загружен в базу из %s", settings.lexicon_path)
        lexicon = Lexicon(await db.read_words(conn))
        app.state.settings = settings
        app.state.db = conn
        app.state.lexicon = lexicon
        app.state.engine = Engine(lexicon, UsageTracker())
        app.state.generate_limiter = RateLimiter(limit=10, window=1.0)
        app.state.like_limiter = RateLimiter(limit=30, window=60.0)
        yield


app = FastAPI(title="Генератор сюжетов", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT_DIR / "app" / "templates")
templates.env.globals["version"] = __version__


# --- зависимости ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Client:
    key: str
    voter_hash: str


def client(request: Request, response: Response) -> Client:
    """Идентификатор клиента для лимитов и голосования.

    Голос — хэш cookie с солью; без cookie (первый запрос, отключённые cookie) —
    хэш IP + User-Agent. Cookie выставляется, если его ещё нет.
    """
    salt = request.app.state.settings.secret_salt
    voter_id = request.cookies.get(VOTER_COOKIE)
    if voter_id and len(voter_id) <= 64:
        source = f"cookie:{voter_id}"
    else:
        ip = request.client.host if request.client else "unknown"
        source = f"ip:{ip}|ua:{request.headers.get('user-agent', '')}"
        response.set_cookie(
            VOTER_COOKIE,
            secrets.token_urlsafe(24),
            max_age=VOTER_COOKIE_AGE,
            httponly=True,
            samesite="lax",
        )
    digest = hashlib.sha256(f"{salt}|{source}".encode()).hexdigest()
    return Client(key=digest[:32], voter_hash=digest)


ClientDep = Annotated[Client, Depends(client)]


# --- сериализация ---------------------------------------------------------------------------


def phrase_url(request: Request, slug: str) -> str:
    return f"{request.app.state.settings.base_url}/s/{slug}"


def phrase_out(request: Request, phrase: Phrase, likes: int = 0) -> PhraseOut:
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
        url=phrase_url(request, phrase.slug),
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


async def stored_phrase(request: Request, slug: str) -> tuple[dict, Phrase | None]:
    """Фраза из базы и, если слова ещё есть в словаре, её пересборка из ключа."""
    row = await db.get_phrase(request.app.state.db, slug)
    if row is None:
        raise HTTPException(404, "фраза не найдена")
    preset_id, slots = slots_from_key(row["key"])
    preset = PRESETS.get(preset_id)
    try:
        phrase = render(request.app.state.lexicon, preset, slots) if preset else None
    except (RenderError, GenerationError):
        phrase = None
    return row, phrase


# --- API ------------------------------------------------------------------------------------


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz(response: Response) -> str:
    response.headers["X-App-Version"] = __version__
    return "ok"


@app.post("/api/generate", response_model=PhraseOut)
async def generate(payload: GenerateIn, request: Request, who: ClientDep) -> PhraseOut:
    if not request.app.state.generate_limiter.allow(who.key):
        raise HTTPException(429, "слишком часто")
    if payload.preset_id not in PRESETS:
        raise HTTPException(422, "неизвестный пресет")
    engine: Engine = request.app.state.engine
    try:
        phrase = await run_in_threadpool(
            engine.generate,
            payload.preset_id,
            current=[Slot(**slot.model_dump()) for slot in payload.slots],
            locked=payload.locked_indices,
            reroll=payload.reroll_index,
            recent_ids=payload.recent_ids,
            tone=None if payload.tone in (None, "auto") else payload.tone,
            wild=payload.wild,
            seed=payload.seed,
        )
    except GenerationError as error:
        log.error("генерация не удалась: %s", error)
        raise HTTPException(500, "не удалось собрать фразу") from error
    await db.remember_phrase(
        request.app.state.db, phrase.slug, phrase.preset.id, phrase.key(), phrase.text
    )
    return phrase_out(request, phrase)


@app.get("/api/phrase/{slug}", response_model=PhraseOut)
async def get_phrase(slug: str, request: Request) -> PhraseOut:
    row, phrase = await stored_phrase(request, slug)
    if phrase is None:
        raise HTTPException(410, "слова этой фразы больше нет в словаре")
    return phrase_out(request, phrase, likes=row["likes"])


@app.post("/api/save", response_model=SaveOut)
async def save(payload: SaveIn, request: Request) -> SaveOut:
    if await db.get_phrase(request.app.state.db, payload.slug) is None:
        raise HTTPException(404, "фраза не найдена")
    likes = await db.save_idea(request.app.state.db, payload.slug)
    return SaveOut(id=payload.slug, slug=payload.slug, likes=likes)


@app.post("/api/like/{slug}", response_model=LikeOut)
async def like(slug: str, request: Request, who: ClientDep) -> LikeOut:
    if not request.app.state.like_limiter.allow(who.key):
        raise HTTPException(429, "слишком часто")
    if await db.get_phrase(request.app.state.db, slug) is None:
        raise HTTPException(404, "фраза не найдена")
    likes, counted = await db.like(request.app.state.db, slug, who.voter_hash)
    return LikeOut(likes=likes, counted=counted)


@app.get("/api/feed", response_model=FeedOut)
async def feed(
    request: Request,
    sort: Literal["top", "new"] = "top",
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 20,
) -> FeedOut:
    rows, next_cursor = await db.feed(request.app.state.db, sort, cursor, limit)
    items = [
        FeedItem(
            slug=row["slug"],
            url=phrase_url(request, row["slug"]),
            text=row["text"],
            likes=row["likes"],
            preset_id=row["preset_id"],
            preset_title=PRESETS[row["preset_id"]].title if row["preset_id"] in PRESETS else "",
            saved_at=row["saved_at"],
        )
        for row in rows
    ]
    return FeedOut(items=items, next_cursor=next_cursor)


@app.get("/api/og-image/{slug}.png")
async def og_image(
    slug: str, request: Request, format: Literal["wide", "square"] = "wide"
) -> FileResponse:
    """PNG-открытка. Фраза по slug не меняется, поэтому кэш вечный и без инвалидации."""
    if not slug.isalnum() or len(slug) > 16:
        raise HTTPException(404, "фраза не найдена")
    settings = request.app.state.settings
    path = settings.og_cache_dir / f"{slug}_{format}.png"
    if not path.exists():
        row, phrase = await stored_phrase(request, slug)
        preset = PRESETS.get(row["preset_id"])
        png = await run_in_threadpool(
            og.render_card,
            row["text"],
            preset.title if preset else "",
            phrase.card_tone if phrase else None,
            format,
            urlsplit(settings.base_url).netloc or settings.base_url,
            slug,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(png)
        tmp.replace(path)
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# --- страницы -------------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, start: Annotated[str | None, Query(alias="from")] = None):
    presets = [{"id": p.id, "title": p.title} for p in PRESETS.values()]
    start_slug = start if start and start.isalnum() and len(start) <= 16 else None
    return templates.TemplateResponse(
        request, "index.html", {"presets": presets, "start_slug": start_slug}
    )


@app.get("/feed", response_class=HTMLResponse)
async def feed_page(request: Request):
    return templates.TemplateResponse(request, "feed.html", {})


@app.get("/s/{slug}", response_class=HTMLResponse)
async def phrase_page(slug: str, request: Request):
    if not slug.isalnum() or len(slug) > 16:
        raise HTTPException(404, "фраза не найдена")
    row, _ = await stored_phrase(request, slug)
    preset = PRESETS.get(row["preset_id"])
    base_url = request.app.state.settings.base_url
    context = {
        "slug": slug,
        "text": row["text"],
        "typo_text": typograph(row["text"]),
        "likes": row["likes"],
        "preset_title": preset.title if preset else "Сюжет",
        "url": f"{base_url}/s/{slug}",
        "image_url": f"{base_url}/api/og-image/{slug}.png",
    }
    return templates.TemplateResponse(request, "card.html", context)
