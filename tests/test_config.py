from app.config import Settings, load_or_create_salt


def test_base_url_from_env(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://example.test/")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "app.up.railway.app")
    assert Settings().base_url == "https://example.test"


def test_base_url_from_railway_domain(monkeypatch):
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "app.up.railway.app")
    assert Settings().base_url == "https://app.up.railway.app"


def test_base_url_default(monkeypatch):
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    assert Settings().base_url == "http://localhost:8000"


def test_salt_is_created_once(tmp_path):
    data_dir = tmp_path / "data"
    salt = load_or_create_salt(data_dir)
    assert len(salt) == 64
    assert (data_dir / "secret_salt").stat().st_mode & 0o777 == 0o600
    assert load_or_create_salt(data_dir) == salt
