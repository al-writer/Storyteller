import html
import re

import pytest

from tests.conftest import generate

pytestmark = pytest.mark.usefixtures("no_rate_limits")


def meta(page: str, name: str) -> str:
    match = re.search(rf'<meta (?:property|name)="{re.escape(name)}" content="([^"]*)"', page)
    assert match, name
    return html.unescape(match.group(1))


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "generator(" in response.text
    assert '"logline"' in response.text
    assert "alpinejs@3.14.8" in response.text


def test_index_ignores_bad_start_slug(client):
    response = client.get("/", params={"from": "<script>"})
    assert response.status_code == 200
    assert "<script>" not in response.text.split("generator(", 1)[1].split(")", 1)[0]


def test_phrase_page_has_open_graph_tags(client):
    data = generate(client, preset_id="setup", seed=40)
    page = client.get(f"/s/{data['slug']}").text
    assert meta(page, "og:description") == data["text"]
    assert meta(page, "og:title") == "Завязка"
    assert meta(page, "og:url") == f"https://example.test/s/{data['slug']}"
    assert meta(page, "og:image") == f"https://example.test/api/og-image/{data['slug']}.png"
    assert meta(page, "twitter:card") == "summary_large_image"
    assert f"/?from={data['slug']}" in page


def test_phrase_page_unknown_slug(client):
    assert client.get("/s/unknown1").status_code == 404
    assert client.get("/s/bad-slug").status_code == 404


def test_feed_page_and_static(client):
    assert client.get("/feed").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/fonts/PTSerif-Regular.ttf").status_code == 200
