from app.ratelimit import RateLimiter


def test_rate_limiter_sliding_window():
    now = [0.0]
    limiter = RateLimiter(limit=2, window=1.0, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 1.0
    assert limiter.allow("a")
