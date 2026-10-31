"""Админка — Telegram Mini App: страница и API отчёта.

Страница открывается кнопкой в боте только у админов, но адрес не секрет, поэтому
доступ решает подпись: каждый запрос несёт ``initData`` Mini App, сервер проверяет
её токеном бота, свежесть и Telegram ID из ``TELEGRAM_ADMIN_IDS``.
"""

from __future__ import annotations

import time
from typing import Annotated

from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app import db
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
