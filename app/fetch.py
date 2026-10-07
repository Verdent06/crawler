"""HTTP fetching with robots.txt, politeness, size caps, and SSRF guards."""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from pathlib import Path

import httpx
import tldextract

USER_AGENT = "PursuitLinkScraper/0.1 (+https://github.com/local/take-home; research)"
MAX_BODY_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0
MAX_DELAY_SECONDS = 10.0
MIN_DELAY_SECONDS = 1.0

_TLD_CACHE = Path(__file__).resolve().parent.parent / "data" / "tld_cache"
_TLD_CACHE.mkdir(parents=True, exist_ok=True)
_EXTRACTOR = tldextract.TLDExtract(cache_dir=str(_TLD_CACHE))


class UnsafeURLError(ValueError):
    """Raised when a URL points at a non-public address."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    error: str | None = None


def registrable_domain(url: str) -> str:
    extracted = _EXTRACTOR(url)
    if not extracted.domain or not extracted.suffix:
        host = urlparse(url).hostname or ""
        return host.lower()
    return f"{extracted.domain}.{extracted.suffix}".lower()


def same_site(seed_url: str, candidate_url: str) -> bool:
    return registrable_domain(seed_url) == registrable_domain(candidate_url)


def _hostname_is_public(hostname: str) -> None:
    if not hostname:
        raise UnsafeURLError("missing hostname")
    host = hostname.strip().lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"}:
        raise UnsafeURLError(f"blocked host: {host}")
    # Block obvious metadata / local names before DNS.
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        raise UnsafeURLError(f"blocked host: {host}")

    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise UnsafeURLError(f"non-public IP: {host}")
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host: {host}") from exc

    if not infos:
        raise UnsafeURLError(f"cannot resolve host: {host}")

    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if not ip.is_global:
            raise UnsafeURLError(f"resolves to non-public IP: {addr}")


def assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"unsupported scheme: {parsed.scheme}")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with credentials are not allowed")
    _hostname_is_public(parsed.hostname or "")


class RobotsCache:
    def __init__(
        self,
        client: httpx.Client,
        user_agent: str = USER_AGENT,
        resolve_check: Callable[[str], None] = assert_public_url,
    ) -> None:
        self.client = client
        self.user_agent = user_agent
        self.resolve_check = resolve_check
        self._parsers: dict[str, RobotFileParser] = {}
        self._delays: dict[str, float] = {}

    def _load(self, base_url: str) -> RobotFileParser:
        domain = registrable_domain(base_url)
        if domain in self._parsers:
            return self._parsers[domain]
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        delay = MIN_DELAY_SECONDS
        try:
            current = robots_url
            resp = None
            for _ in range(5):
                self.resolve_check(current)
                resp = self.client.get(current, follow_redirects=False, timeout=15.0)
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("location")
                    if not location:
                        break
                    current = urljoin(current, location)
                    continue
                break
            if resp is None or resp.status_code >= 400:
                rp.parse([])
            else:
                text = resp.text
                rp.parse(text.splitlines())
                for line in text.splitlines():
                    lower = line.strip().lower()
                    if lower.startswith("crawl-delay:"):
                        try:
                            delay = float(lower.split(":", 1)[1].strip())
                        except ValueError:
                            pass
        except Exception:
            rp.parse([])
        delay = max(MIN_DELAY_SECONDS, min(delay, MAX_DELAY_SECONDS))
        self._parsers[domain] = rp
        self._delays[domain] = delay
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._load(url)
        # Match both our UA and the wildcard group.
        return rp.can_fetch(self.user_agent, url) and rp.can_fetch("*", url)

    def delay(self, url: str) -> float:
        self._load(url)
        return self._delays.get(registrable_domain(url), MIN_DELAY_SECONDS)


class Fetcher:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        max_body_bytes: int = MAX_BODY_BYTES,
        sleep_fn: Callable[[float], None] = time.sleep,
        resolve_check: Callable[[str], None] = assert_public_url,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            follow_redirects=False,
            timeout=DEFAULT_TIMEOUT,
        )
        self.max_body_bytes = max_body_bytes
        self.sleep_fn = sleep_fn
        self.resolve_check = resolve_check
        self.robots = RobotsCache(self.client, resolve_check=resolve_check)
        self._last_fetch_at: dict[str, float] = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _wait(self, url: str) -> None:
        domain = registrable_domain(url)
        delay = self.robots.delay(url)
        last = self._last_fetch_at.get(domain, 0.0)
        remaining = delay - (time.monotonic() - last)
        if remaining > 0:
            self.sleep_fn(remaining)

    def fetch(self, url: str, *, respect_robots: bool = True) -> FetchResult:
        self.resolve_check(url)
        if respect_robots and not self.robots.allowed(url):
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                body=b"",
                error="blocked by robots.txt",
            )

        self._wait(url)
        current = url
        body = b""
        content_type = ""
        status_code = 0
        try:
            for _ in range(8):
                self.resolve_check(current)
                with self.client.stream("GET", current) as resp:
                    status_code = resp.status_code
                    content_type = resp.headers.get("content-type", "")
                    if status_code in {301, 302, 303, 307, 308}:
                        location = resp.headers.get("location")
                        if not location:
                            raise UnsafeURLError("redirect without Location")
                        next_url = urljoin(current, location)
                        self.resolve_check(next_url)
                        current = next_url
                        continue

                    cl = resp.headers.get("content-length")
                    if cl is not None:
                        try:
                            if int(cl) > self.max_body_bytes:
                                return FetchResult(
                                    url=url,
                                    final_url=str(resp.url),
                                    status_code=status_code,
                                    content_type=content_type,
                                    body=b"",
                                    error=f"content-length exceeds {self.max_body_bytes} bytes",
                                )
                        except ValueError:
                            pass

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > self.max_body_bytes:
                            return FetchResult(
                                url=url,
                                final_url=str(resp.url),
                                status_code=status_code,
                                content_type=content_type,
                                body=b"",
                                error=f"body exceeds {self.max_body_bytes} bytes",
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    final_url = str(resp.url)
                    self._last_fetch_at[registrable_domain(url)] = time.monotonic()
                    error = None
                    if status_code >= 400:
                        error = f"HTTP {status_code}"
                    return FetchResult(
                        url=url,
                        final_url=final_url,
                        status_code=status_code,
                        content_type=content_type,
                        body=body if error is None else b"",
                        error=error,
                    )
            return FetchResult(
                url=url,
                final_url=current,
                status_code=status_code,
                content_type=content_type,
                body=b"",
                error="too many redirects",
            )
        except UnsafeURLError:
            raise
        except Exception as exc:
            self._last_fetch_at[registrable_domain(url)] = time.monotonic()
            return FetchResult(
                url=url,
                final_url=current,
                status_code=status_code,
                content_type=content_type,
                body=b"",
                error=str(exc),
            )
