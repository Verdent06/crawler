"""DNS fallback used when the OS resolver fails."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.resolve import (
    addrinfo_from_ips,
    dns_fallback_enabled,
    install_dns_fallback,
    install_dns_fallback_from_env,
    lookup_ips,
)


def test_addrinfo_from_ips_uses_requested_port():
    records = addrinfo_from_ips(["150.171.110.210"], 443)
    assert records[0][4] == ("150.171.110.210", 443)


def test_lookup_uses_direct_dns_when_asked(monkeypatch):
    reply = _a_reply(b"www.a2gov.org", "150.171.110.210")

    def serve(sock: socket.socket) -> None:
        data, addr = sock.recvfrom(512)
        sock.sendto(data[:2] + reply[2:], addr)

    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    host, port = server.getsockname()
    thread = threading.Thread(target=serve, args=(server,))
    thread.start()
    monkeypatch.setattr("app.resolve.nameservers", lambda: [host])
    monkeypatch.setattr("app.resolve._CACHE", {})

    original_udp = __import__("app.resolve", fromlist=["_udp"])._udp

    def udp(server_ip: str, query: bytes) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(2.0)
            sock.sendto(query, (server_ip, port))
            data, _ = sock.recvfrom(4096)
            return data
        finally:
            sock.close()

    monkeypatch.setattr("app.resolve._udp", udp)
    try:
        assert lookup_ips("www.a2gov.org") == ["150.171.110.210"]
    finally:
        thread.join(timeout=2)
        server.close()
        monkeypatch.setattr("app.resolve._udp", original_udp)


def test_socket_fallback_returns_direct_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    install_dns_fallback()
    monkeypatch.setattr("app.resolve.lookup_ips", lambda host: ["150.171.110.210"])

    def fail(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        raise socket.gaierror(socket.EAI_NONAME, "no")

    monkeypatch.setattr("app.resolve._ORIGINAL", fail)
    infos = socket.getaddrinfo("www.a2gov.org", 443)
    assert infos[0][4][0] == "150.171.110.210"


def test_fallback_is_off_by_default(monkeypatch):
    monkeypatch.delenv("DNS_FALLBACK", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    before = socket.getaddrinfo
    assert dns_fallback_enabled() is False
    assert install_dns_fallback_from_env() is False
    assert socket.getaddrinfo is before


def test_importing_fetch_does_not_patch_resolver(tmp_path):
    code = (
        "import socket, app.fetch, app.main;"
        "print(getattr(socket.getaddrinfo, '_dns_fallback', False))"
    )
    env = {k: v for k, v in os.environ.items() if k != "DNS_FALLBACK"}
    env["SCRAPER_DB_PATH"] = str(tmp_path / "import.db")
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parent.parent,
        check=True,
    )
    assert out.stdout.strip() == "False"


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_env_flag_enables_install(monkeypatch, value):
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    monkeypatch.setenv("DNS_FALLBACK", value)
    assert install_dns_fallback_from_env() is True
    assert socket.getaddrinfo._dns_fallback is True


@pytest.mark.parametrize("value", ["", "0", "false", "off"])
def test_env_flag_values_that_stay_off(monkeypatch, value):
    monkeypatch.setenv("DNS_FALLBACK", value)
    assert dns_fallback_enabled() is False


def _a_reply(name: bytes, ip: str) -> bytes:
    header = b"\x00\x00\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
    question = b"".join(bytes([len(part)]) + part for part in name.split(b".")) + b"\x00"
    question += b"\x00\x01\x00\x01"
    answer = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + socket.inet_aton(ip)
    return header + question + answer
