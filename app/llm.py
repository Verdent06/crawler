"""Optional Llama client via Ollama. Falls back silently when unavailable."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
DEFAULT_URL = os.environ.get("OLLAMA_URL", "").rstrip("/")


class LlamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else DEFAULT_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self._client = client
        self._available: bool | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def available(self) -> bool:
        if not self.enabled:
            return False
        if self._available is not None:
            return self._available
        try:
            client = self._client or httpx.Client(timeout=3.0)
            owns = self._client is None
            try:
                resp = client.get(f"{self.base_url}/api/tags")
                self._available = resp.status_code == 200
            finally:
                if owns:
                    client.close()
        except Exception:
            self._available = False
        return self._available

    def _chat_json(self, prompt: str) -> dict[str, Any] | None:
        if not self.available():
            return None
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You classify municipal and school finance web links. "
                        "Reply with JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        try:
            client = self._client or httpx.Client(timeout=self.timeout)
            owns = self._client is None
            try:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                if resp.status_code != 200:
                    return None
                content = resp.json().get("message", {}).get("content", "")
                return json.loads(content)
            finally:
                if owns:
                    client.close()
        except Exception:
            return None

    def rerank_link(
        self, *, url: str, anchor_text: str, context: str
    ) -> dict[str, Any] | None:
        prompt = (
            "Score this link for municipal/school finance relevance.\n"
            f"URL: {url}\n"
            f"Anchor: {anchor_text}\n"
            f"Context: {context[:500]}\n"
            'Return JSON: {"link_type":"document|contact|navigation",'
            '"result_score":0-100,"reason":"short"}'
        )
        return self._chat_json(prompt)

    def extract_contacts(self, page_text: str) -> list[dict[str, Any]]:
        prompt = (
            "Extract finance-related contacts from this page text. "
            "Only include people tied to finance, budget, treasurer, controller, or CFO.\n"
            f"Text:\n{page_text[:3500]}\n"
            'Return JSON: {"contacts":[{"name":"","title":"","email":"","phone":""}]}'
        )
        data = self._chat_json(prompt)
        if not data:
            return []
        contacts = data.get("contacts") or []
        if not isinstance(contacts, list):
            return []
        return [c for c in contacts if isinstance(c, dict)]

    def confirm_document(self, text: str, url: str) -> dict[str, Any] | None:
        prompt = (
            "Does this PDF excerpt look like an ACFR/CAFR or a budget document?\n"
            f"URL: {url}\n"
            f"Excerpt:\n{text[:3000]}\n"
            'Return JSON: {"claimed_type":"acfr|budget|other|unknown",'
            '"fiscal_year":"YYYY|null","verdict":"confirmed|mismatch|unreadable",'
            '"title":"short","evidence":"short"}'
        )
        return self._chat_json(prompt)


def parse_json_loose(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
