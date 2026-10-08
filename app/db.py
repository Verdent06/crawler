"""SQLite persistence for crawl results."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


CONTACT_PAGE_REASON = "extracted finance contact from page"
UNCHECKED_EVIDENCE = "document link found but not downloaded"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seed_url TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    pages_fetched INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(seed_url)
                );

                CREATE TABLE IF NOT EXISTS pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    url TEXT NOT NULL,
                    status_code INTEGER,
                    content_type TEXT,
                    fetched_at TEXT,
                    error TEXT,
                    UNIQUE(site_id, url)
                );

                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    url TEXT NOT NULL,
                    source_page TEXT,
                    anchor_text TEXT,
                    link_type TEXT NOT NULL,
                    follow_score REAL NOT NULL DEFAULT 0,
                    result_score REAL NOT NULL DEFAULT 0,
                    matched_keywords TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(site_id, url)
                );

                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    source_url TEXT NOT NULL,
                    name TEXT,
                    title TEXT,
                    email TEXT,
                    phone TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(site_id, source_url, email, name)
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                    url TEXT NOT NULL,
                    claimed_type TEXT,
                    fiscal_year TEXT,
                    title TEXT,
                    verdict TEXT NOT NULL,
                    evidence TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(site_id, url)
                );

                CREATE INDEX IF NOT EXISTS idx_links_site_score
                    ON links(site_id, result_score DESC);
                CREATE INDEX IF NOT EXISTS idx_links_type
                    ON links(link_type);
                CREATE INDEX IF NOT EXISTS idx_sites_domain
                    ON sites(domain);
                """
            )

    def create_or_reset_site(self, seed_url: str, domain: str) -> int:
        now = utc_now()
        with self.connection() as conn:
            existing = conn.execute(
                "SELECT id FROM sites WHERE seed_url = ?", (seed_url,)
            ).fetchone()
            if existing:
                site_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE sites
                    SET status = 'running', error = NULL, pages_fetched = 0,
                        domain = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (domain, now, site_id),
                )
                for table in ("pages", "links", "contacts", "documents"):
                    conn.execute(f"DELETE FROM {table} WHERE site_id = ?", (site_id,))
                return site_id
            cur = conn.execute(
                """
                INSERT INTO sites (seed_url, domain, status, created_at, updated_at)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (seed_url, domain, now, now),
            )
            return int(cur.lastrowid)

    def update_site(
        self,
        site_id: int,
        *,
        status: str | None = None,
        error: str | None = None,
        pages_fetched: int | None = None,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [utc_now()]
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        if pages_fetched is not None:
            fields.append("pages_fetched = ?")
            values.append(pages_fetched)
        values.append(site_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE sites SET {', '.join(fields)} WHERE id = ?", values
            )

    def upsert_page(
        self,
        site_id: int,
        url: str,
        *,
        status_code: int | None = None,
        content_type: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO pages (site_id, url, status_code, content_type, fetched_at, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id, url) DO UPDATE SET
                    status_code = excluded.status_code,
                    content_type = excluded.content_type,
                    fetched_at = excluded.fetched_at,
                    error = excluded.error
                """,
                (site_id, url, status_code, content_type, utc_now(), error),
            )

    def upsert_link(
        self,
        site_id: int,
        *,
        url: str,
        source_page: str | None,
        anchor_text: str | None,
        link_type: str,
        follow_score: float,
        result_score: float,
        matched_keywords: list[str],
        reason: str,
    ) -> None:
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO links (
                    site_id, url, source_page, anchor_text, link_type,
                    follow_score, result_score, matched_keywords, reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id, url) DO UPDATE SET
                    source_page = excluded.source_page,
                    anchor_text = excluded.anchor_text,
                    link_type = excluded.link_type,
                    follow_score = excluded.follow_score,
                    result_score = excluded.result_score,
                    matched_keywords = excluded.matched_keywords,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                WHERE links.reason != ? OR excluded.reason = links.reason
                """,
                (
                    site_id,
                    url,
                    source_page,
                    anchor_text,
                    link_type,
                    follow_score,
                    result_score,
                    json.dumps(matched_keywords),
                    reason,
                    now,
                    now,
                    CONTACT_PAGE_REASON,
                ),
            )

    def upsert_contact(
        self,
        site_id: int,
        *,
        source_url: str,
        name: str | None,
        title: str | None,
        email: str | None,
        phone: str | None,
    ) -> None:
        if not any([name, title, email, phone]):
            return
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO contacts (
                    site_id, source_url, name, title, email, phone,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id, source_url, email, name) DO UPDATE SET
                    title = COALESCE(excluded.title, contacts.title),
                    phone = COALESCE(excluded.phone, contacts.phone),
                    updated_at = excluded.updated_at
                """,
                (site_id, source_url, name, title, email, phone, now, now),
            )

    def upsert_document(
        self,
        site_id: int,
        *,
        url: str,
        claimed_type: str | None,
        fiscal_year: str | None,
        title: str | None,
        verdict: str,
        evidence: str | None,
    ) -> None:
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    site_id, url, claimed_type, fiscal_year, title, verdict,
                    evidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id, url) DO UPDATE SET
                    claimed_type = excluded.claimed_type,
                    fiscal_year = excluded.fiscal_year,
                    title = excluded.title,
                    verdict = excluded.verdict,
                    evidence = excluded.evidence,
                    updated_at = excluded.updated_at
                """,
                (
                    site_id,
                    url,
                    claimed_type,
                    fiscal_year,
                    title,
                    verdict,
                    evidence,
                    now,
                    now,
                ),
            )

    def record_unchecked_documents(self, site_id: int) -> int:
        now = utc_now()
        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO documents (
                    site_id, url, claimed_type, fiscal_year, title, verdict,
                    evidence, created_at, updated_at
                )
                SELECT l.site_id, l.url, NULL, NULL, l.anchor_text, 'skipped',
                       ?, ?, ?
                FROM links l
                WHERE l.site_id = ? AND l.link_type = 'document'
                  AND NOT EXISTS (
                      SELECT 1 FROM documents d
                      WHERE d.site_id = l.site_id AND d.url = l.url
                  )
                """,
                (UNCHECKED_EVIDENCE, now, now, site_id),
            )
            return cur.rowcount

    def list_sites(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sites ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_site_id_by_seed(self, seed_url: str) -> int | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id FROM sites WHERE seed_url = ?", (seed_url,)
            ).fetchone()
            return int(row["id"]) if row else None

    def get_site(self, site_id: int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sites WHERE id = ?", (site_id,)
            ).fetchone()
            if not row:
                return None
            site = dict(row)
            site["links"] = [
                self._decode_link(r)
                for r in conn.execute(
                    """
                    SELECT * FROM links
                    WHERE site_id = ?
                    ORDER BY result_score DESC, follow_score DESC
                    """,
                    (site_id,),
                ).fetchall()
            ]
            site["contacts"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM contacts WHERE site_id = ? ORDER BY id",
                    (site_id,),
                ).fetchall()
            ]
            site["documents"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM documents WHERE site_id = ? ORDER BY id",
                    (site_id,),
                ).fetchall()
            ]
            return site

    def query_links(
        self,
        *,
        domain: str | None = None,
        link_type: str | None = None,
        min_score: float | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if domain:
            clauses.append("s.domain = ?")
            params.append(domain)
        if link_type:
            clauses.append("l.link_type = ?")
            params.append(link_type)
        if min_score is not None:
            clauses.append("l.result_score >= ?")
            params.append(min_score)
        if q:
            clauses.append(
                "(l.url LIKE ? OR l.anchor_text LIKE ? OR l.matched_keywords LIKE ?)"
            )
            like = f"%{q}%"
            params.extend([like, like, like])
        params.extend([limit, offset])
        sql = f"""
            SELECT l.*, s.domain, s.seed_url
            FROM links l
            JOIN sites s ON s.id = l.site_id
            WHERE {' AND '.join(clauses)}
            ORDER BY l.result_score DESC
            LIMIT ? OFFSET ?
        """
        with self.connection() as conn:
            return [self._decode_link(r) for r in conn.execute(sql, params).fetchall()]

    @staticmethod
    def _decode_link(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        raw = item.get("matched_keywords")
        if isinstance(raw, str):
            try:
                item["matched_keywords"] = json.loads(raw)
            except json.JSONDecodeError:
                item["matched_keywords"] = [raw]
        return item
