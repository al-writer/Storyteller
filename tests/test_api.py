import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import VOTER_COOKIE, app
from tests.conftest import generate

pytestmark = pytest.mark.usefixtures("no_rate_limits")


def test_healthz(client):
    response = client.get("/healthz")
    assert response.text == "ok"
    assert response.headers["X-App-Version"]


def test_generate_returns_phrase_and_sets_cookie(client):
    response = client.post("/api/generate", json={"preset_id": "plot", "seed": 1})
    assert response.status_code == 200
    data = response.json()
    assert data["text"].endswith(".")
    assert len(data["slug"]) == 8
    assert data["url"] == f"https://example.test/s/{data['slug']}"
    assert [s["name"] for s in data["slots"]][:2] == ["adj_actor", "actor"]
    assert all(s["text"] for s in data["slots"])
    assert data["text"] in data["prompt"]["story"]
    assert VOTER_COOKIE in client.cookies


def test_permalink_works_for_any_generated_phrase(client):
    data = generate(client, preset_id="mystery", seed=2)
    stored = client.get(f"/api/phrase/{data['slug']}").json()
    assert stored["text"] == data["text"]
    assert stored["slots"] == data["slots"]


def test_same_seed_gives_same_slug(client):
    assert (
        generate(client, preset_id="setup", seed=3)["slug"]
        == generate(client, preset_id="setup", seed=3)["slug"]
    )


def test_locks_and_reroll(client):
    first = generate(client, preset_id="plot", seed=4)
    slots = [{k: s[k] for k in ("id", "number", "gender", "superlative")} for s in first["slots"]]
    locked = generate(client, preset_id="plot", slots=slots, locked_indices=[1, 4], seed=5)
    assert locked["slots"][1]["id"] == first["slots"][1]["id"]
    assert locked["slots"][4]["id"] == first["slots"][4]["id"]
    rerolled = generate(client, preset_id="plot", slots=slots, reroll_index=1, seed=6)
    changed = [
        i for i, (a, b) in enumerate(zip(first["slots"], rerolled["slots"], strict=True))
        if a["id"] != b["id"]
    ]  # fmt: skip
    assert changed == [1]


def test_generate_validates_input(client):
    assert client.post("/api/generate", json={"preset_id": "nope"}).status_code == 422
    assert client.post("/api/generate", json={"tone": "pink"}).status_code == 422
    # Текст фразы не принимается: лишнее поле просто игнорируется.
    data = generate(client, preset_id="plot", seed=7, text="<script>alert(1)</script>")
    assert "script" not in data["text"]


def test_save_accepts_only_slug(client):
    data = generate(client, preset_id="logline", seed=8)
    response = client.post("/api/save", json={"slug": data["slug"], "text": "подделка"})
    assert response.status_code == 200
    assert response.json() == {"id": data["slug"], "slug": data["slug"], "likes": 0}
    assert client.get(f"/api/phrase/{data['slug']}").json()["text"] == data["text"]
    assert client.post("/api/save", json={"text": "подделка"}).status_code == 422
    assert client.post("/api/save", json={"slug": "unknown1"}).status_code == 404
    assert client.post("/api/save", json={"slug": "<b>x</b>"}).status_code == 422


def test_repeated_like_does_not_change_counter(client):
    data = generate(client, preset_id="absurd", seed=9)
    first = client.post(f"/api/like/{data['slug']}").json()
    second = client.post(f"/api/like/{data['slug']}").json()
    assert first == {"likes": 1, "counted": True}
    assert second == {"likes": 1, "counted": False}
    other = TestClient(app)  # без with: lifespan уже запущен общим клиентом
    other.cookies.set(VOTER_COOKIE, "another-voter")
    assert other.post(f"/api/like/{data['slug']}").json() == {"likes": 2, "counted": True}
    assert client.post("/api/like/unknown1").status_code == 404


def test_feed_new_and_top(client):
    slugs = []
    for seed in range(20, 25):
        slug = generate(client, preset_id="conflict", seed=seed)["slug"]
        client.post("/api/save", json={"slug": slug})
        slugs.append(slug)
    client.post(f"/api/like/{slugs[0]}")

    page = client.get("/api/feed", params={"sort": "new", "limit": 2}).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"]
    seen = [item["slug"] for item in page["items"]]
    while page["next_cursor"]:
        page = client.get(
            "/api/feed", params={"sort": "new", "limit": 2, "cursor": page["next_cursor"]}
        ).json()
        seen += [item["slug"] for item in page["items"]]
    assert set(slugs) <= set(seen)
    assert len(seen) == len(set(seen))

    top = client.get("/api/feed", params={"sort": "top"}).json()["items"]
    assert slugs[0] in [item["slug"] for item in top]
    assert all(item["likes"] > 0 for item in top)
    assert client.get("/api/feed", params={"limit": 31}).status_code == 422


def test_rate_limit_on_generate(client):
    app.state.generate_limiter.limit = 10
    app.state.generate_limiter._hits.clear()
    codes = [client.post("/api/generate", json={}).status_code for _ in range(12)]
    assert codes.count(429) >= 1
    assert codes[:10] == [200] * 10


def test_og_image_is_generated_and_cached(client):
    data = generate(client, preset_id="plot", seed=30)
    for fmt, size in (("wide", (1200, 630)), ("square", (1080, 1080))):
        response = client.get(f"/api/og-image/{data['slug']}.png", params={"format": fmt})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert "immutable" in response.headers["cache-control"]
        assert Image.open(io.BytesIO(response.content)).size == size
        cached = app.state.settings.og_cache_dir / f"{data['slug']}_{fmt}.png"
        assert cached.read_bytes() == response.content
    assert client.get("/api/og-image/unknown1.png").status_code == 404
    assert client.get(f"/api/og-image/{data['slug']}.png?format=huge").status_code == 422
