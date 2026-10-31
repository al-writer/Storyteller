"""Админка — Telegram Mini App: страница и API отчёта.

Страница открывается кнопкой в боте только у админов, но адрес не секрет, поэтому
доступ решает подпись: каждый запрос несёт ``initData`` Mini App, сервер проверяет
её токеном бота, свежесть и Telegram ID из ``TELEGRAM_ADMIN_IDS``.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Annotated, Literal

from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app import __version__, db, service
from app.config import Settings
from app.presets import PRESETS

INIT_DATA_HEADER = "X-Telegram-Init-Data"
INIT_DATA_TTL = 24 * 3600  # Mini App держат открытой подолгу; дольше суток — пусть откроют заново

router = APIRouter()


def admin_id(
    request: Request,
    init_data: Annotated[str, Header(alias=INIT_DATA_HEADER)] = "",
) -> int:
    settings: Settings = request.app.state.settings
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
