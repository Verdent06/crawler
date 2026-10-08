"""Hostname lookup that asks the configured DNS servers directly.

macOS can return "cannot resolve host" from getaddrinfo while those
servers still answer. httpx uses getaddrinfo, so the fallback has to
sit underneath it.
"""

from __future__ import annotations

import os
import socket
import struct
import time
from pathlib import Path

_CACHE: dict[str, tuple[float, list[str]]] = {}
_CACHE_SECONDS = 30.0
_ORIGINAL = socket.getaddrinfo


def nameservers() -> list[str]:
    servers: list[str] = []
    try:
        text = Path("/etc/resolv.conf").read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            servers.append(parts[1])
    return servers or ["1.1.1.1", "9.9.9.9"]


def lookup_ips(host: str) -> list[str]:
    key = host.rstrip(".").lower()
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < _CACHE_SECONDS:
        return list(cached[1])

    found: list[str] = []
    for server in nameservers():
        for qtype in (1, 28):
            try:
                found.extend(_query(server, key, qtype))
            except OSError:
                continue
            if found:
                break
        if found:
            break
    unique = list(dict.fromkeys(found))
    if unique:
        _CACHE[key] = (now, unique)
    return unique


def addrinfo_from_ips(
    ips: list[str],
    port: int | str | None,
    family: int = 0,
    socktype: int = 0,
    proto: int = 0,
) -> list[tuple]:
    port_num = _port_number(port)
    chosen_type = socktype or socket.SOCK_STREAM
    chosen_proto = proto or socket.IPPROTO_TCP
    records = []
    for ip in ips:
        packed = socket.inet_pton(socket.AF_INET6 if ":" in ip else socket.AF_INET, ip)
        if len(packed) == 4:
            fam = socket.AF_INET
            sockaddr: tuple = (ip, port_num)
        else:
            fam = socket.AF_INET6
            sockaddr = (ip, port_num, 0, 0)
        if family not in (0, fam):
            continue
        records.append((fam, chosen_type, chosen_proto, "", sockaddr))
    return records


def dns_fallback_enabled() -> bool:
    return os.environ.get("DNS_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}


def install_dns_fallback_from_env() -> bool:
    if not dns_fallback_enabled():
        return False
    install_dns_fallback()
    return True


def install_dns_fallback() -> None:
    current = socket.getaddrinfo
    if getattr(current, "_dns_fallback", False):
        return

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        try:
            return _ORIGINAL(host, port, family, type, proto, flags)
        except socket.gaierror:
            if not host:
                raise
            ips = lookup_ips(str(host))
            records = addrinfo_from_ips(ips, port, family, type, proto)
            if not records:
                raise
            return records

    getaddrinfo._dns_fallback = True  # type: ignore[attr-defined]
    socket.getaddrinfo = getaddrinfo


def _port_number(port: int | str | None) -> int:
    if port is None or port == "":
        return 0
    if isinstance(port, int):
        return port
    try:
        return int(port)
    except ValueError:
        return socket.getservbyname(port)


def _query(server: str, host: str, qtype: int) -> list[str]:
    query = _build_query(host, qtype)
    data = _exchange(server, query)
    return _answers(data, query[:2], qtype)


def _build_query(host: str, qtype: int) -> bytes:
    header = struct.pack("!HHHHHH", int.from_bytes(os.urandom(2), "big"), 0x0100, 1, 0, 0, 0)
    return header + _encode_name(host) + struct.pack("!HH", qtype, 1)


def _encode_name(host: str) -> bytes:
    encoded = host.encode("idna")
    return b"".join(bytes([len(label)]) + label for label in encoded.split(b".") if label) + b"\x00"


def _exchange(server: str, query: bytes) -> bytes:
    data = _udp(server, query)
    if len(data) >= 4 and int.from_bytes(data[2:4], "big") & 0x0200:
        data = _tcp(server, query)
    if len(data) < 12:
        raise OSError(f"short dns response from {server}")
    return data


def _udp(server: str, query: bytes) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(2.0)
        sock.sendto(query, (server, 53))
        data, _ = sock.recvfrom(4096)
        return data
    finally:
        sock.close()


def _tcp(server: str, query: bytes) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(2.0)
        sock.connect((server, 53))
        sock.sendall(struct.pack("!H", len(query)) + query)
        header = _recv_exact(sock, 2)
        return _recv_exact(sock, int.from_bytes(header, "big"))
    finally:
        sock.close()


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining:
        piece = sock.recv(remaining)
        if not piece:
            raise OSError("truncated dns response")
        chunks.append(piece)
        remaining -= len(piece)
    return b"".join(chunks)


def _answers(data: bytes, tid: bytes, qtype: int) -> list[str]:
    if data[:2] != tid:
        return []
    flags, qdcount, ancount = struct.unpack("!HHHHHH", data[:12])[1:4]
    if flags & 0x000F:
        return []
    offset = 12
    for _ in range(qdcount):
        offset = _skip_name(data, offset) + 4
    ips: list[str] = []
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        atype, _aclass, _ttl, rdlen = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlen]
        offset += rdlen
        if atype == qtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(rdata))
        elif atype == qtype == 28 and rdlen == 16:
            ips.append(socket.inet_ntop(socket.AF_INET6, rdata))
    return ips


def _skip_name(packet: bytes, offset: int) -> int:
    hops = 0
    while hops < 20:
        if offset >= len(packet):
            raise OSError("truncated dns name")
        length = packet[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length
        hops += 1
    raise OSError("dns name loop")
