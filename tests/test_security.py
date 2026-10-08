"""API key, rate limit, and concurrency guards on POST /scrape."""

from __future__ import annotations

import importlib
import pytest
from fastapi.testclient import TestClient

from app.security import SlidingWindowLimiter, api_key_matches, client_ip, env_flag, env_int

BODY = {"url": "https://example.gov/"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _load(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("SCRAPER_DB_PATH", str(tmp_path / "sec.db"))
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    for name in (
        "SCRAPER_API_KEY",
        "SCRAPE_RATE_LIMIT_PER_MINUTE",
        "MAX_CONCURRENT_SCRAPES",
        "TRUST_FORWARDED_FOR",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    import app.main as main

    return importlib.reload(main)


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    clients: list[TestClient] = []

    def build(**env):
        main = _load(tmp_path, monkeypatch, **env)
        monkeypatch.setattr(main, "assert_public_url", lambda url: None)
        monkeypatch.setattr(main.CrawlRunner, "run", lambda self, *a, **k: None)
        client = TestClient(main.app)
        client.__enter__()
        clients.append(client)
        return client, main

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def test_no_key_required_when_unset(make_client):
    client, _ = make_client()
    assert client.post("/scrape", json=BODY).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
def test_wrong_or_missing_key_is_401(make_client, headers):
    client, _ = make_client(SCRAPER_API_KEY="s3cret")
    resp = client.post("/scrape", json=BODY, headers=headers)
    assert resp.status_code == 401
    assert "s3cret" not in resp.text


def test_right_key_is_accepted(make_client):
    client, _ = make_client(SCRAPER_API_KEY="s3cret")
    resp = client.post("/scrape", json=BODY, headers={"X-API-Key": "s3cret"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def test_health_and_reads_stay_open(make_client):
    client, _ = make_client(SCRAPER_API_KEY="s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/sites").status_code == 200


def test_unauthorized_requests_do_not_consume_rate_limit(make_client):
    client, _ = make_client(SCRAPER_API_KEY="k", SCRAPE_RATE_LIMIT_PER_MINUTE="1")
    for _ in range(3):
        assert client.post("/scrape", json=BODY).status_code == 401
    assert client.post("/scrape", json=BODY, headers={"X-API-Key": "k"}).status_code == 200


def test_rate_limit_returns_429_with_retry_after(make_client):
    client, main = make_client(SCRAPE_RATE_LIMIT_PER_MINUTE="2")
    clock = FakeClock()
    main.rate_limiter = SlidingWindowLimiter(2, clock=clock)

    assert client.post("/scrape", json=BODY).status_code == 200
    clock.now += 10
    assert client.post("/scrape", json=BODY).status_code == 200
    clock.now += 10
    blocked = client.post("/scrape", json=BODY)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "40"

    clock.now += 41
    assert client.post("/scrape", json=BODY).status_code == 200


def test_rate_limit_zero_disables(make_client):
    client, _ = make_client(SCRAPE_RATE_LIMIT_PER_MINUTE="0")
    for _ in range(15):
        assert client.post("/scrape", json=BODY).status_code == 200


def test_concurrency_cap_rejects_new_crawls(make_client):
    client, main = make_client(MAX_CONCURRENT_SCRAPES="2", SCRAPE_RATE_LIMIT_PER_MINUTE="0")
    with main._jobs_lock:
        main._running_sites.update({9001, 9002})

    resp = client.post("/scrape", json=BODY)

    assert resp.status_code == 503
    assert "busy" in resp.json()["detail"]
    assert resp.headers["Retry-After"]
    assert client.get("/sites").json() == []


def test_concurrency_cap_still_reports_already_running_site(make_client):
    client, main = make_client(MAX_CONCURRENT_SCRAPES="1", SCRAPE_RATE_LIMIT_PER_MINUTE="0")
    site_id = main.db.create_or_reset_site(BODY["url"], "example.gov")
    with main._jobs_lock:
        main._running_sites.add(site_id)

    resp = client.post("/scrape", json=BODY)

    assert resp.status_code == 200
    assert resp.json()["site_id"] == site_id


def test_limiter_is_per_client():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, clock=clock)
    assert limiter.retry_after("a") == 0
    assert limiter.retry_after("a") == 60
    assert limiter.retry_after("b") == 0


def test_limiter_sweeps_idle_clients():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, clock=clock)
    for i in range(1100):
        limiter.retry_after(f"client-{i}")
    clock.now += 61
    limiter.retry_after("fresh")
    assert len(limiter._hits) < 10


def test_api_key_matches_handles_missing_and_unicode():
    assert api_key_matches("abc", "abc")
    assert not api_key_matches("abc", None)
    assert not api_key_matches("abc", "abd")
    assert api_key_matches("clé", "clé")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 7), ("", 7), ("3", 3), ("0", 0), ("-2", 0), ("nope", 7)],
)
def test_env_int(monkeypatch, raw, expected):
    monkeypatch.delenv("SOME_LIMIT", raising=False)
    if raw is not None:
        monkeypatch.setenv("SOME_LIMIT", raw)
    assert env_int("SOME_LIMIT", 7) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, False), ("", False), ("0", False), ("no", False), ("1", True), ("true", True), ("YES", True), ("on", True)],
)
def test_env_flag(monkeypatch, raw, expected):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    if raw is not None:
        monkeypatch.setenv("SOME_FLAG", raw)
    assert env_flag("SOME_FLAG") is expected


def test_client_ip_ignores_forwarded_headers_by_default():
    headers = {
        "cf-connecting-ip": "203.0.113.9",
        "x-forwarded-for": "198.51.100.1",
    }
    assert client_ip(headers, "10.0.0.8", trust_forwarded=False) == "10.0.0.8"


def test_client_ip_prefers_cloudflare_over_xff_when_trusted():
    headers = {
        "cf-connecting-ip": "203.0.113.9",
        "x-forwarded-for": "198.51.100.1, 203.0.113.9",
    }
    assert client_ip(headers, "10.0.0.8", trust_forwarded=True) == "203.0.113.9"


def test_client_ip_falls_back_to_xff_then_peer():
    assert (
        client_ip({"x-forwarded-for": "198.51.100.7, 10.1.1.1"}, "10.0.0.8", trust_forwarded=True)
        == "198.51.100.7"
    )
    assert client_ip({}, "10.0.0.8", trust_forwarded=True) == "10.0.0.8"
    assert client_ip({}, None, trust_forwarded=False) == "unknown"


def test_spoofed_xff_ignored_when_flag_off(make_client):
    client, main = make_client(SCRAPE_RATE_LIMIT_PER_MINUTE="1")
    clock = FakeClock()
    main.rate_limiter = SlidingWindowLimiter(1, clock=clock)

    assert client.post("/scrape", json=BODY, headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200
    blocked = client.post("/scrape", json=BODY, headers={"X-Forwarded-For": "203.0.113.11"})
    assert blocked.status_code == 429


def test_forwarded_client_ip_used_when_flag_on(make_client):
    client, main = make_client(TRUST_FORWARDED_FOR="1", SCRAPE_RATE_LIMIT_PER_MINUTE="1")
    clock = FakeClock()
    main.rate_limiter = SlidingWindowLimiter(1, clock=clock)

    first = {"CF-Connecting-IP": "203.0.113.10"}
    second = {"CF-Connecting-IP": "203.0.113.11"}
    assert client.post("/scrape", json=BODY, headers=first).status_code == 200
    assert client.post("/scrape", json=BODY, headers=first).status_code == 429
    assert client.post("/scrape", json=BODY, headers=second).status_code == 200
