"""Админка: подпись initData Mini App, отчёт и журнал событий."""

import hashlib
import hmac
import json
import time
from dataclasses import replace
from urllib.parse import urlencode

import pytest

from app import db
from app.main import app
from tests.conftest import generate

pytestmark = pytest.mark.usefixtures("no_rate_limits")

TOKEN = "42:TEST"
ADMIN_ID = 438452289


def init_data(user_id: int, token: str = TOKEN, auth_date: float | None = None) -> str:
    """initData так, как её подписывает Telegram."""
    fields = {
        "auth_date": str(int(time.time() if auth_date is None else auth_date)),
        "query_id": "q1",
        "user": json.dumps({"id": user_id, "first_name": "А"}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def admin_settings(client):
    settings = app.state.settings
    app.state.settings = replace(
        settings, telegram_token=TOKEN, telegram_admin_ids=frozenset({ADMIN_ID})
    )
    yield
    app.state.settings = settings


def report(client, data: str | None, **params):
    headers = {"X-Telegram-Init-Data": data} if data is not None else {}
    return client.get("/api/admin/report", params=params, headers=headers)


@pytest.mark.usefixtures("admin_settings")
def test_report_needs_valid_admin_signature(client):
    assert report(client, None).status_code == 403
    assert report(client, init_data(ADMIN_ID, token="43:OTHER")).status_code == 403
    assert report(client, init_data(ADMIN_ID, auth_date=time.time() - 2 * 86400)).status_code == 403
    assert report(client, init_data(ADMIN_ID + 1)).status_code == 403
    assert report(client, init_data(ADMIN_ID)).status_code == 200
    assert report(client, init_data(ADMIN_ID), days=3).status_code == 422


def test_report_without_bot_is_closed(client):
    assert report(client, init_data(ADMIN_ID)).status_code == 403


@pytest.mark.usefixtures("admin_settings")
def test_report_counts_web_events(client):
    before = report(client, init_data(ADMIN_ID), days=7).json()
    data = generate(client, preset_id="mystery", seed=70)
    client.post("/api/save", json={"slug": data["slug"]})
    client.post(f"/api/like/{data['slug']}")
    after = report(client, init_data(ADMIN_ID), days=7).json()

    assert len(after["days"]) == 7
    assert after["generated"]["web"][-1] == before["generated"]["web"][-1] + 1
    assert after["likes"]["web"][-1] >= before["likes"]["web"][-1]
    assert after["active"]["web"][-1] >= 1
    assert any(p["id"] == "mystery" and p["title"] == "Тайна" for p in after["presets"])
    assert "top" not in after["summary"]
    assert all({"slug", "text", "likes", "preset_title"} <= set(row) for row in after["top"])


def test_admin_page_is_a_shell(client):
    page = client.get("/admin")
    assert page.status_code == 200
    assert "telegram-web-app.js" in page.text
    assert "noindex" in page.text


def test_report_series_by_day(client):
    async def scenario():
        now = 10 * 86400 + 3600  # день 10, 01:00 UTC
        await db.log_event(app.state.db, "generate", "tg", "a", "plot", now=now)
        await db.log_event(app.state.db, "reroll", "tg", "a", "plot", now=now)
        await db.log_event(app.state.db, "generate", "web", "b", "plot", now=now - 86400)
        await db.log_event(app.state.db, "like", "tg", "c", now=now - 30 * 86400)
        return await db.admin_report(app.state.db, 7, now=now)

    data = client.portal.call(scenario)
    assert data["days"][-1] == "1970-01-11"
    assert data["generated"]["tg"][-1] == 2
    assert data["generated"]["web"][-2] == 1
    assert data["active"]["tg"][-1] == 1
    assert sum(data["likes"]["tg"]) == 0  # лайк старше периода


def test_old_tg_users_table_gets_tg_id(tmp_path):
    import asyncio

    async def scenario():
        async with db.connect(tmp_path / "old.sqlite3") as conn:
            await conn.execute(
                "CREATE TABLE tg_users (user_hash TEXT PRIMARY KEY, preset_id TEXT, "
                "tone TEXT NOT NULL DEFAULT 'auto', wild INTEGER NOT NULL DEFAULT 0, "
                "recent TEXT NOT NULL DEFAULT '[]', last_message_id INTEGER, last_slug TEXT, "
                "last_mask INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, "
                "seen_at REAL NOT NULL)"
            )
            await db.create_schema(conn)
            await db.create_schema(conn)  # повторный запуск не ломается
            user = await db.tg_user(conn, "h", tg_id=7)
            await db.save_tg_user(conn, user)
            return await db.tg_user(conn, "h")

    assert asyncio.run(scenario())["tg_id"] == 7


def admin_call(client, method: str, path: str, **params):
    headers = {"X-Telegram-Init-Data": init_data(ADMIN_ID)}
    return client.request(method, path, params=params, headers=headers)


@pytest.mark.usefixtures("admin_settings")
def test_moderation_hides_and_resets(client):
    data = generate(client, preset_id="setup", seed=80)
    slug = data["slug"]
    client.post("/api/save", json={"slug": slug})
    client.post(f"/api/like/{slug}")

    found = admin_call(client, "GET", "/api/admin/phrases", q=data["text"][:12].upper()).json()
    assert slug in [item["slug"] for item in found["items"]]  # поиск без учёта регистра

    assert admin_call(client, "POST", f"/api/admin/phrases/{slug}/hide").status_code == 200
    new = client.get("/api/feed", params={"sort": "new", "limit": 30}).json()["items"]
    assert slug not in [item["slug"] for item in new]
    saved = admin_call(client, "GET", "/api/admin/phrases", scope="saved", limit=50).json()
    row = next(item for item in saved["items"] if item["slug"] == slug)
    assert row["hidden"] and row["saved"]

    assert admin_call(client, "POST", f"/api/admin/phrases/{slug}/show").status_code == 200
    assert admin_call(client, "POST", f"/api/admin/phrases/{slug}/reset-likes").status_code == 200
    assert client.get(f"/api/phrase/{slug}").json()["likes"] == 0
    assert client.post(f"/api/like/{slug}").json()["counted"]  # голос снова засчитывается

    other = generate(client, preset_id="setup", seed=81)["slug"]  # не в зале славы
    assert admin_call(client, "POST", f"/api/admin/phrases/{other}/hide").status_code == 404
    assert client.post(f"/api/admin/phrases/{slug}/hide").status_code == 403


@pytest.mark.usefixtures("admin_settings")
def test_service_and_cache(client):
    slug = generate(client, preset_id="plot", seed=82)["slug"]
    client.get(f"/api/og-image/{slug}.png")
    info = admin_call(client, "GET", "/api/admin/service").json()
    assert info["cache_files"] >= 1 and info["db_bytes"] > 0
    assert info["webhook"] is None  # бот в тестах не запущен
    assert {"words", "phrases", "hidden", "events", "tg_reachable"} <= set(info["counts"])

    cleared = admin_call(client, "POST", "/api/admin/cache/clear").json()
    assert cleared["files"] == info["cache_files"]
    assert admin_call(client, "GET", "/api/admin/service").json()["cache_files"] == 0
    assert client.get(f"/api/og-image/{slug}.png").status_code == 200  # перерисовалась
