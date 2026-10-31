"""Админка — Telegram Mini App и права админа на сайте.

Адрес админки не секрет, поэтому доступ решает подпись. Из Mini App каждый запрос
несёт ``initData``: сервер проверяет её токеном бота, свежесть и Telegram ID из
``TELEGRAM_ADMIN_IDS``. На сайте админ входит одноразовой ссылкой из бота и получает
подписанную cookie; запросы с ней, кроме GET, должны нести заголовок ``X-Admin-Request``
— чужой сайт не может его поставить без CORS, это защита от CSRF.
"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import time
from pathlib import Path
from typing import Annotated, Literal

from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import __version__, db, media, service
from app.bot.broadcast import Busy
from app.config import Settings
from app.presets import PRESETS

INIT_DATA_HEADER = "X-Telegram-Init-Data"
INIT_DATA_TTL = 24 * 3600  # Mini App держат открытой подолгу; дольше суток — пусть откроют заново

router = APIRouter()


SESSION_COOKIE = "st_admin"
SESSION_AGE = 30 * 24 * 3600
CSRF_HEADER = "X-Admin-Request"


def _signature(salt: str, payload: str) -> str:
    return hmac.new(salt.encode(), f"admin|{payload}".encode(), hashlib.sha256).hexdigest()


def session_value(salt: str, user_id: int, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + SESSION_AGE)
    payload = f"{user_id}.{expires}"
    return f"{payload}.{_signature(salt, payload)}"


def site_admin_id(request: Request) -> int | None:
    """Telegram ID админа из cookie сайта, если подпись цела, срок не вышел и он ещё админ."""
    settings: Settings = request.app.state.settings
    user_id, _, rest = request.cookies.get(SESSION_COOKIE, "").partition(".")
    expires, _, signature = rest.partition(".")
    if not (user_id.isdigit() and expires.isdigit()):
        return None
    expected = _signature(settings.secret_salt, f"{user_id}.{expires}")
    if not hmac.compare_digest(signature, expected) or int(expires) < time.time():
        return None
    return int(user_id) if int(user_id) in settings.telegram_admin_ids else None


def admin_id(
    request: Request,
    init_data: Annotated[str, Header(alias=INIT_DATA_HEADER)] = "",
) -> int:
    settings: Settings = request.app.state.settings
    if not init_data:
        user_id = site_admin_id(request)
        if user_id is not None and (
            request.method == "GET" or request.headers.get(CSRF_HEADER) == "1"
        ):
            return user_id
    if not settings.telegram_token or not init_data:
        raise HTTPException(403, "откройте админку из Telegram")
    try:
        data = safe_parse_webapp_init_data(settings.telegram_token, init_data)
    except ValueError as error:
        raise HTTPException(403, "подпись Telegram не сошлась") from error
    if time.time() - data.auth_date.timestamp() > INIT_DATA_TTL:
        raise HTTPException(403, "данные устарели — откройте админку заново")
    if data.user is None or data.user.id not in settings.telegram_admin_ids:
        raise HTTPException(403, "нет доступа")
    return data.user.id


AdminDep = Annotated[int, Depends(admin_id)]


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page(request: Request):
    from app.main import templates  # шаблоны живут в main; импорт здесь — без цикла

    return templates.TemplateResponse(request, "admin.html", {})


@router.get("/api/admin/report", include_in_schema=False)
async def admin_report(
    request: Request, _: AdminDep, days: Annotated[int, Query(ge=7, le=90)] = 30
) -> dict:
    report = await db.admin_report(request.app.state.db, days)
    report["presets"] = [
        {"id": preset_id, "title": PRESETS[preset_id].title if preset_id in PRESETS else preset_id,
         "n": n}
        for preset_id, n in report["presets"]
    ]  # fmt: skip
    for row in report["top"]:
        preset = PRESETS.get(row.pop("preset_id"))
        row["preset_title"] = preset.title if preset else ""
    report["summary"].pop("top")  # дублирует top
    return report


# --- модерация ------------------------------------------------------------------------------


@router.get("/api/admin/phrases", include_in_schema=False)
async def admin_phrases(
    request: Request,
    _: AdminDep,
    scope: Literal["saved", "all"] = "saved",
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    rows, total = await db.admin_phrases(request.app.state.db, scope, q.strip(), limit, offset)
    for row in rows:
        preset = PRESETS.get(row.pop("preset_id"))
        row["preset_title"] = preset.title if preset else ""
    return {"items": rows, "total": total}


async def _phrase_action(request: Request, slug: str, action) -> dict:
    if not service.valid_slug(slug) or not await action(request.app.state.db, slug):
        raise HTTPException(404, "фразы нет в зале славы")
    return {"ok": True}


@router.post("/api/admin/phrases/{slug}/hide", include_in_schema=False)
async def hide_phrase(slug: str, request: Request, _: AdminDep) -> dict:
    return await _phrase_action(request, slug, lambda conn, s: db.set_hidden(conn, s, True))


@router.post("/api/admin/phrases/{slug}/show", include_in_schema=False)
async def show_phrase(slug: str, request: Request, _: AdminDep) -> dict:
    return await _phrase_action(request, slug, lambda conn, s: db.set_hidden(conn, s, False))


@router.post("/api/admin/phrases/{slug}/reset-likes", include_in_schema=False)
async def reset_likes(slug: str, request: Request, _: AdminDep) -> dict:
    return await _phrase_action(request, slug, db.reset_likes)


# --- сервис ---------------------------------------------------------------------------------


def dir_size(path: Path) -> tuple[int, int]:
    """Размер каталога в байтах и число файлов; нет каталога — нули."""
    files = [p for p in path.rglob("*") if p.is_file()] if path.exists() else []
    return sum(p.stat().st_size for p in files), len(files)


@router.get("/api/admin/service", include_in_schema=False)
async def admin_service(request: Request, _: AdminDep) -> dict:
    state = request.app.state
    settings: Settings = state.settings
    db_bytes = sum(
        path.stat().st_size
        for path in (settings.db_path, settings.db_path.with_name(settings.db_path.name + "-wal"))
        if path.exists()
    )
    cache_bytes, cache_files = dir_size(settings.og_cache_dir)
    webhook = None
    telegram = getattr(state, "telegram", None)
    if telegram is not None:
        try:
            info = await telegram.bot.get_webhook_info()
            webhook = {
                "url": info.url,
                "pending": info.pending_update_count,
                "last_error_at": info.last_error_date.timestamp() if info.last_error_date else None,
                "last_error": info.last_error_message,
            }
        except Exception as error:  # Telegram недоступен — показываем, а не падаем
            webhook = {"error": str(error)}
    return {
        "version": __version__,
        "base_url": settings.base_url,
        "bot": service.bot_handle(state),
        "started_at": getattr(state, "started_at", None),
        "now": time.time(),
        "db_bytes": db_bytes,
        "cache_bytes": cache_bytes,
        "cache_files": cache_files,
        "counts": await db.table_counts(state.db),
        "webhook": webhook,
    }


@router.post("/api/admin/cache/clear", include_in_schema=False)
async def clear_cache(request: Request, _: AdminDep) -> dict:
    """Удаляет кэш открыток: они перерисуются при следующем запросе."""
    cache_dir = request.app.state.settings.og_cache_dir
    freed, files = dir_size(cache_dir)
    shutil.rmtree(cache_dir, ignore_errors=True)
    return {"freed_bytes": freed, "files": files}


# --- рассылка -------------------------------------------------------------------------------


class BroadcastIn(BaseModel):
    text: str = Field(min_length=1, max_length=4096)  # предел длины сообщения в Telegram


def broadcaster(request: Request):
    telegram = getattr(request.app.state, "telegram", None)
    if telegram is None:
        raise HTTPException(503, "бот не запущен")
    return telegram.broadcaster


@router.get("/api/admin/broadcasts", include_in_schema=False)
async def broadcasts(request: Request, _: AdminDep) -> dict:
    telegram = getattr(request.app.state, "telegram", None)
    return {
        "recipients": len(await db.broadcast_recipients(request.app.state.db)),
        "running": bool(telegram and telegram.broadcaster.running),
        "items": await db.list_broadcasts(request.app.state.db),
    }


@router.post("/api/admin/broadcast/test", include_in_schema=False)
async def broadcast_test(payload: BroadcastIn, request: Request, admin: AdminDep) -> dict:
    """Пробное сообщение — только себе, чтобы увидеть его глазами до рассылки."""
    try:
        await broadcaster(request).send(admin, payload.text.strip())
    except Exception as error:
        raise HTTPException(502, f"Telegram не принял сообщение: {error}") from error
    return {"ok": True}


@router.post("/api/admin/broadcast", include_in_schema=False)
async def broadcast_start(payload: BroadcastIn, request: Request, _: AdminDep) -> dict:
    try:
        broadcast_id = await broadcaster(request).start(payload.text.strip())
    except Busy as error:
        raise HTTPException(409, "рассылка уже идёт") from error
    return {"id": broadcast_id}


@router.post("/api/admin/broadcast/{broadcast_id}/cancel", include_in_schema=False)
async def broadcast_cancel(broadcast_id: int, request: Request, _: AdminDep) -> dict:
    if not broadcaster(request).cancel(broadcast_id):
        raise HTTPException(409, "эта рассылка не идёт")
    return {"ok": True}


# --- вход на сайт ---------------------------------------------------------------------------


@router.get("/admin/login", include_in_schema=False)
async def site_login(request: Request, token: str = ""):
    user_id, expires = service.take_login_token(request.app.state, token)
    settings: Settings = request.app.state.settings
    if user_id is None or expires < time.time() or user_id not in settings.telegram_admin_ids:
        return PlainTextResponse(
            "Ссылка устарела или уже использована — возьмите новую под /stats в боте.", 403
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session_value(settings.secret_salt, user_id),
        max_age=SESSION_AGE,
        httponly=True,
        secure=settings.base_url.startswith("https://"),
        samesite="lax",
    )
    return response


@router.get("/admin/logout", include_in_schema=False)
async def site_logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- рассказ и картинка к фразе -------------------------------------------------------------


class StoryIn(BaseModel):
    story: str = Field(max_length=20_000)


def extra_out(extra: dict) -> dict:
    image = extra["image"]
    return {"story": extra["story"], "image_url": f"/media/{image}" if image else None}


async def existing_slug(request: Request, slug: str) -> str:
    if not service.valid_slug(slug) or await db.get_phrase(request.app.state.db, slug) is None:
        raise HTTPException(404, "фраза не найдена")
    return slug


@router.get("/api/admin/phrases/{slug}/extra", include_in_schema=False)
async def get_extra(slug: str, request: Request, _: AdminDep) -> dict:
    await existing_slug(request, slug)
    return extra_out(await db.get_extra(request.app.state.db, slug))


@router.put("/api/admin/phrases/{slug}/story", include_in_schema=False)
async def put_story(slug: str, payload: StoryIn, request: Request, _: AdminDep) -> dict:
    await existing_slug(request, slug)
    extra = await db.save_extra(request.app.state.db, slug, story=payload.story.strip())
    return extra_out(extra)


@router.put("/api/admin/phrases/{slug}/image", include_in_schema=False)
async def put_image(slug: str, request: Request, _: AdminDep) -> dict:
    """Тело запроса — сам файл картинки (без multipart)."""
    await existing_slug(request, slug)
    if int(request.headers.get("content-length") or 0) > media.MAX_UPLOAD:
        raise HTTPException(413, "картинка больше 15 МБ")
    data = await request.body()
    if not data or len(data) > media.MAX_UPLOAD:
        raise HTTPException(
            413 if data else 422, "картинка больше 15 МБ" if data else "пустой файл"
        )
    media_dir = request.app.state.settings.media_dir
    try:
        name = await run_in_threadpool(media.store, data, media_dir, slug)
    except media.BadImage as error:
        raise HTTPException(422, str(error)) from error
    old = (await db.get_extra(request.app.state.db, slug))["image"]
    extra = await db.save_extra(request.app.state.db, slug, image=name)
    if old != name:
        media.remove(media_dir, old)
    return extra_out(extra)


@router.delete("/api/admin/phrases/{slug}/image", include_in_schema=False)
async def delete_image(slug: str, request: Request, _: AdminDep) -> dict:
    await existing_slug(request, slug)
    old = (await db.get_extra(request.app.state.db, slug))["image"]
    extra = await db.save_extra(request.app.state.db, slug, image=None)
    media.remove(request.app.state.settings.media_dir, old)
    return extra_out(extra)
