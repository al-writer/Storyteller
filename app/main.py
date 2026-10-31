"""FastAPI-приложение: роуты и жизненный цикл."""

from __future__ import annotations

import logging
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__, admin, db, media, service
from app.bot import SECRET_HEADER, WEBHOOK_PATH, TelegramBot
from app.config import (
    ROOT_DIR,
    Settings,
    get_settings,
    load_or_create_salt,
    load_or_create_secret,
)
from app.engine import Engine
from app.lexicon import Lexicon, UsageTracker
from app.presets import PRESETS
from app.ratelimit import RateLimiter
from app.schemas import FeedOut, GenerateIn, LikeOut, PhraseOut, SaveIn, SaveOut
from app.service import Client
from app.typo import typograph

log = logging.getLogger(__name__)

VOTER_COOKIE = "st_vid"
VOTER_COOKIE_AGE = 60 * 60 * 24 * 365 * 2


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    if not settings.secret_salt:
        settings = replace(settings, secret_salt=load_or_create_salt(settings.data_dir))
    async with db.connect(settings.db_path) as conn:
        await db.create_schema(conn)
        await db.interrupt_broadcasts(conn)
        if await db.load_lexicon(conn, settings.lexicon_path):
            log.info("словарь загружен в базу из %s", settings.lexicon_path)
        lexicon = Lexicon(await db.read_words(conn))
        app.state.settings = settings
        app.state.db = conn
        app.state.lexicon = lexicon
        app.state.engine = Engine(lexicon, UsageTracker())
        app.state.generate_limiter = RateLimiter(limit=10, window=1.0)
        app.state.like_limiter = RateLimiter(limit=30, window=60.0)
        app.state.started_at = time.time()
        app.state.telegram = await start_telegram(app.state)
        try:
            yield
        finally:
            if app.state.telegram:
                await app.state.telegram.stop()


async def start_telegram(state) -> TelegramBot | None:
    settings: Settings = state.settings
    if not settings.telegram_token:
        return None
    if not settings.base_url.startswith("https://"):
        # Webhook нужен публичный HTTPS. Локальный запуск с боевым токеном webhook не трогает.
        log.warning("Telegram-бот выключен: BASE_URL %s не HTTPS", settings.base_url)
        return None
    secret = settings.telegram_webhook_secret or load_or_create_secret(
        settings.data_dir, "tg_webhook_secret"
    )
    telegram = TelegramBot(state, settings.telegram_token, secret)
    await telegram.start(f"{settings.base_url}{WEBHOOK_PATH}")
    return telegram


app = FastAPI(title="Генератор сюжетов", version=__version__, lifespan=lifespan)
app.include_router(admin.router)
app.mount("/static", StaticFiles(directory=ROOT_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT_DIR / "app" / "templates")
templates.env.globals["version"] = __version__
# Имя бота известно только после его старта, поэтому функция, а не значение.
templates.env.globals["telegram_bot"] = lambda: getattr(
    getattr(app.state, "telegram", None), "username", None
)


# --- зависимости ----------------------------------------------------------------------------


def client(request: Request, response: Response) -> Client:
    """Идентификатор клиента для лимитов и голосования.

    Голос — хэш cookie с солью; без cookie (первый запрос, отключённые cookie) —
    хэш IP + User-Agent. Cookie выставляется, если его ещё нет.
    """
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
    return Client.from_source(request.app.state.settings.secret_salt, source)


ClientDep = Annotated[Client, Depends(client)]

ERROR_STATUS: dict[type[service.ServiceError], int] = {
    service.TooFast: 429,
    service.NotFound: 404,
    service.Gone: 410,
    service.Invalid: 422,
    service.Failed: 500,
}


@app.exception_handler(service.ServiceError)
async def service_error(request: Request, error: service.ServiceError) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=ERROR_STATUS.get(type(error), 500))


# --- API ------------------------------------------------------------------------------------


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz(response: Response) -> str:
    response.headers["X-App-Version"] = __version__
    return "ok"


@app.post("/api/generate", response_model=PhraseOut)
async def generate(payload: GenerateIn, request: Request, who: ClientDep) -> PhraseOut:
    return await service.generate(request.app.state, who, payload)


@app.get("/api/phrase/{slug}", response_model=PhraseOut)
async def get_phrase(slug: str, request: Request) -> PhraseOut:
    return await service.get_phrase(request.app.state, slug)


@app.post("/api/save", response_model=SaveOut)
async def save(payload: SaveIn, request: Request) -> SaveOut:
    return await service.save(request.app.state, payload.slug)


@app.post("/api/like/{slug}", response_model=LikeOut)
async def like(slug: str, request: Request, who: ClientDep) -> LikeOut:
    return await service.like(request.app.state, who, slug)


@app.get("/api/feed", response_model=FeedOut)
async def feed(
    request: Request,
    sort: Literal["top", "new"] = "top",
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 20,
) -> FeedOut:
    return await service.feed(request.app.state, sort, cursor, limit)


@app.get("/api/og-image/{slug}.png")
async def og_image(
    slug: str,
    request: Request,
    format: Literal["wide", "square"] = "wide",
    via: Literal["site", "tg"] = "site",
) -> FileResponse:
    """PNG-открытка. Фраза по slug не меняется, поэтому кэш вечный и без инвалидации.

    ``via=tg`` — открытка для Telegram: в углу ``@handle`` бота вместо домена.
    """
    mark = service.bot_handle(request.app.state) if via == "tg" else None
    path = await service.card_path(request.app.state, slug, format, mark)
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.post(WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(request: Request) -> Response:
    telegram: TelegramBot | None = request.app.state.telegram
    given = request.headers.get(SECRET_HEADER, "")
    if telegram is None or not secrets.compare_digest(given, telegram.secret):
        raise HTTPException(403, "нет доступа")
    await telegram.feed(await request.json())
    return Response()


# --- страницы -------------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, start: Annotated[str | None, Query(alias="from")] = None):
    presets = [{"id": p.id, "title": p.title} for p in PRESETS.values()]
    start_slug = start if start and service.valid_slug(start) else None
    context = {
        "presets": presets,
        "start_slug": start_slug,
        "site_admin": admin.site_admin_id(request) is not None,
    }
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/feed", response_class=HTMLResponse)
async def feed_page(request: Request):
    return templates.TemplateResponse(request, "feed.html", {})


@app.get("/media/{name}")
async def media_file(name: str, request: Request) -> FileResponse:
    """Картинки к фразам. Имя содержит хэш содержимого, поэтому кэш вечный."""
    path = request.app.state.settings.media_dir / name
    if not media.NAME.match(name) or not path.exists():
        raise HTTPException(404, "нет такой картинки")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/s/{slug}", response_class=HTMLResponse)
async def phrase_page(slug: str, request: Request, via: Literal["site", "tg"] = "site"):
    """Страница фразы. ``via=tg`` — ссылка из Telegram: превью с открыткой бота."""
    if not service.valid_slug(slug):
        raise HTTPException(404, "фраза не найдена")
    row, _ = await service.load(request.app.state, slug)
    preset = PRESETS.get(row["preset_id"])
    base_url = request.app.state.settings.base_url
    extra = await db.get_extra(request.app.state.db, slug)
    paragraphs = [p.strip() for p in extra["story"].replace("\r", "").split("\n\n") if p.strip()]
    context = {
        "story": [typograph(" ".join(p.split())) for p in paragraphs],
        "story_image": f"/media/{extra['image']}" if extra["image"] else None,
        "slug": slug,
        "text": row["text"],
        "typo_text": typograph(row["text"]),
        "likes": row["likes"],
        "preset_title": preset.title if preset else "Сюжет",
        "url": f"{base_url}/s/{slug}",
        "image_url": f"{base_url}/api/og-image/{slug}.png" + ("?via=tg" if via == "tg" else ""),
    }
    return templates.TemplateResponse(request, "card.html", context)
