"""Serve recorded site fixtures through an httpx mock transport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.crawl import CrawlConfig, CrawlRunner
from app.db import Database
from app.fetch import Fetcher
from app.llm import LlmClient

FIXTURES = Path(__file__).parent / "fixtures" / "sites"


def make_pdf(text: str) -> bytes:
    safe = text.replace("\\", " ").replace("(", " ").replace(")", " ")
    stream = f"BT /F1 12 Tf 40 700 Td ({safe}) Tj ET".encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


def load_site(name: str) -> dict:
    directory = FIXTURES / name
    return json.loads((directory / "index.json").read_text(encoding="utf-8"))


def _body(directory: Path, entry: dict) -> bytes:
    if entry.get("pdf_text") is not None:
        return make_pdf(entry["pdf_text"])
    if entry.get("file"):
        return (directory / entry["file"]).read_bytes()
    return b""


def transport_for(name: str) -> httpx.MockTransport:
    directory = FIXTURES / name
    index = load_site(name)

    def handler(request: httpx.Request) -> httpx.Response:
        entry = index.get(str(request.url))
        if entry is None:
            return httpx.Response(404, text="not recorded")
        headers = {"content-type": entry.get("content_type") or ""}
        if entry.get("location"):
            headers["location"] = entry["location"]
        return httpx.Response(
            entry["status"], headers=headers, content=_body(directory, entry)
        )

    return httpx.MockTransport(handler)


def replay_site(db: Database, name: str, seed: str, config: CrawlConfig) -> dict:
    client = httpx.Client(transport=transport_for(name), follow_redirects=False)
    fetcher = Fetcher(
        client=client, sleep_fn=lambda _: None, resolve_check=lambda url: None
    )
    runner = CrawlRunner(db, fetcher=fetcher, llm=LlmClient(base_url=""))
    site_id = runner.run(seed, config)
    runner.close()
    site = db.get_site(site_id)
    assert site is not None
    return site
