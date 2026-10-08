"""SSRF and fetch safety tests."""

from __future__ import annotations

import httpx
import pytest

from app.fetch import Fetcher, UnsafeURLError, assert_public_url


def test_rejects_localhost():
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://127.0.0.1/secret")
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://localhost/admin")


def test_rejects_private_ip():
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://10.0.0.5/meta")
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://192.168.1.1/")


def test_rejects_file_scheme():
    with pytest.raises(UnsafeURLError):
        assert_public_url("file:///etc/passwd")


def test_redirect_to_localhost_is_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"Location": "http://127.0.0.1/secret"}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    fetcher = Fetcher(
        client=client,
        sleep_fn=lambda _: None,
        resolve_check=assert_public_url,
    )
    with pytest.raises(UnsafeURLError):
        fetcher.fetch("https://example.com/start", respect_robots=False)
    fetcher.close()


def test_fetch_caps_oversized_body():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf", "content-length": "1000"},
            content=b"x" * 1000,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    fetcher = Fetcher(
        client=client,
        max_body_bytes=100,
        sleep_fn=lambda _: None,
        resolve_check=lambda url: None,
    )
    result = fetcher.fetch("https://example.com/big", respect_robots=False)
    assert result.error is not None
    assert "exceeds" in result.error
    fetcher.close()


def test_robots_redirect_to_localhost_is_ignored_safely(dns_fallback):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                302, headers={"Location": "http://127.0.0.1/robots.txt"}
            )
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    fetcher = Fetcher(
        client=client,
        sleep_fn=lambda _: None,
        resolve_check=assert_public_url,
    )
    # robots load fails closed to empty allow-all; page fetch still SSRF-checks.
    result = fetcher.fetch("https://example.com/page")
    assert result.status_code == 200
    fetcher.close()


def test_http_error_status_sets_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text="Access Denied", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    fetcher = Fetcher(
        client=client,
        sleep_fn=lambda _: None,
        resolve_check=lambda url: None,
    )
    result = fetcher.fetch("https://example.com/", respect_robots=False)
    assert result.error == "HTTP 403"
    assert result.body == b""
    fetcher.close()


def test_robots_disallow_is_respected():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private\n",
            )
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    fetcher = Fetcher(
        client=client,
        sleep_fn=lambda _: None,
        resolve_check=lambda url: None,
    )
    result = fetcher.fetch("https://example.com/private/secret")
    assert result.error == "blocked by robots.txt"
    fetcher.close()
