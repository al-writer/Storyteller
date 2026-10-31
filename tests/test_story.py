"""Рассказ и картинка к фразе: вход админа на сайт, API, страница фразы."""

import io
from dataclasses import replace

import pytest
from PIL import Image

from app import admin, service
from app.main import app
from tests.conftest import generate

pytestmark = pytest.mark.usefixtures("no_rate_limits")

ADMIN_ID = 438452289
CSRF = {"X-Admin-Request": "1"}


@pytest.fixture
def site_admin(client):
    """Клиент с cookie админа сайта."""
    settings = app.state.settings
    app.state.settings = replace(settings, telegram_admin_ids=frozenset({ADMIN_ID}))
    client.cookies.set(admin.SESSION_COOKIE, admin.session_value(settings.secret_salt, ADMIN_ID))
    yield client
    client.cookies.delete(admin.SESSION_COOKIE)
    app.state.settings = settings


def png(size=(2400, 1200), mode="RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def test_login_link_is_one_time(client):
    settings = app.state.settings
    app.state.settings = replace(settings, telegram_admin_ids=frozenset({ADMIN_ID}))
    try:
        token = service.issue_login_token(app.state, ADMIN_ID)
        response = client.get("/admin/login", params={"token": token}, follow_redirects=False)
        assert response.status_code == 303
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{admin.SESSION_COOKIE}={ADMIN_ID}.")
        assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
        again = client.get("/admin/login", params={"token": token}, follow_redirects=False)
        assert again.status_code == 403
        stranger = service.issue_login_token(app.state, ADMIN_ID + 1)
        assert client.get("/admin/login", params={"token": stranger}).status_code == 403
    finally:
        client.cookies.clear()
        app.state.settings = settings


def test_tampered_or_foreign_cookie_is_not_admin(client):
    settings = app.state.settings
    app.state.settings = replace(settings, telegram_admin_ids=frozenset({ADMIN_ID}))
    try:
        good = admin.session_value(settings.secret_salt, ADMIN_ID)
        for value in (good[:-1] + "0", good.replace(str(ADMIN_ID), str(ADMIN_ID + 1)), "junk"):
            client.cookies.set(admin.SESSION_COOKIE, value)
            assert "storyEditor()" not in client.get("/").text
        expired = admin.session_value(settings.secret_salt, ADMIN_ID, now=0)
        client.cookies.set(admin.SESSION_COOKIE, expired)
        assert "storyEditor()" not in client.get("/").text
    finally:
        client.cookies.clear()
        app.state.settings = settings


def test_panel_only_for_admin(client, site_admin):
    assert "storyEditor()" in client.get("/").text
    client.cookies.delete(admin.SESSION_COOKIE)
    assert "storyEditor()" not in client.get("/").text


def test_story_and_image_end_to_end(client, site_admin):
    data = generate(client, preset_id="plot", seed=90)
    slug, base = data["slug"], f"/api/admin/phrases/{data['slug']}"

    assert client.put(f"{base}/story", json={"story": "x"}).status_code == 403  # без CSRF
    story = "Первый абзац\nпродолжается.\n\nВторой абзац — <b>не</b> разметка."
    saved = client.put(f"{base}/story", json={"story": story}, headers=CSRF).json()
    assert saved == {"story": story, "image_url": None}
    feed = client.get("/api/feed", params={"sort": "new", "limit": 30}).json()["items"]
    assert slug in [item["slug"] for item in feed]  # сохранение отправило в зал славы

    image = client.put(
        f"{base}/image", content=png(), headers={**CSRF, "Content-Type": "image/png"}
    )
    assert image.status_code == 200
    url = image.json()["image_url"]
    served = client.get(url)
    assert served.headers["content-type"] == "image/jpeg"
    assert "immutable" in served.headers["cache-control"]
    assert max(Image.open(io.BytesIO(served.content)).size) == 1600

    page = client.get(f"/s/{slug}").text
    assert "Первый абзац продолжается." in page
    assert "&lt;b&gt;не&lt;/b&gt;" in page
    assert url in page

    second = client.put(f"{base}/image", content=png((300, 300), "RGBA"), headers=CSRF).json()
    assert client.get(url).status_code == 404  # старая картинка удалена
    assert client.delete(f"{base}/image", headers=CSRF).json()["image_url"] is None
    assert client.get(second["image_url"]).status_code == 404

    client.put(f"{base}/story", json={"story": ""}, headers=CSRF)
    assert "История" not in client.get(f"/s/{slug}").text


def test_bad_uploads(client, site_admin):
    slug = generate(client, preset_id="plot", seed=91)["slug"]
    base = f"/api/admin/phrases/{slug}"
    assert client.put(f"{base}/image", content=b"not an image", headers=CSRF).status_code == 422
    assert client.put(f"{base}/image", content=b"", headers=CSRF).status_code == 422
    assert (
        client.put(
            "/api/admin/phrases/unknown1/story", json={"story": "x"}, headers=CSRF
        ).status_code
        == 404
    )
    long = {"story": "x" * 20_001}
    assert client.put(f"{base}/story", json=long, headers=CSRF).status_code == 422
    assert client.get("/media/../secret").status_code == 404


def test_saving_story_unhides_phrase(client, site_admin):
    from app import db

    slug = generate(client, preset_id="plot", seed=92)["slug"]
    client.post("/api/save", json={"slug": slug})
    client.portal.call(db.set_hidden, app.state.db, slug, True)
    client.put(f"/api/admin/phrases/{slug}/story", json={"story": "Рассказ"}, headers=CSRF)
    feed = client.get("/api/feed", params={"sort": "new", "limit": 30}).json()["items"]
    assert slug in [item["slug"] for item in feed]


def test_stats_has_site_login_button(client):
    from tests.test_bot import Chat_

    settings = app.state.settings
    app.state.settings = replace(settings, telegram_admin_ids=frozenset({ADMIN_ID}))
    try:
        chat = Chat_(client, ADMIN_ID)
        chat.send("/stats")
        from aiogram.methods import SendMessage

        markup = chat.session.of(SendMessage)[-1].reply_markup
        login = markup.inline_keyboard[1][0].url
        assert login.startswith("https://example.test/admin/login?token=")
        response = client.get(login.removeprefix("https://example.test"), follow_redirects=False)
        assert response.status_code == 303
    finally:
        client.cookies.clear()
        app.state.settings = settings
