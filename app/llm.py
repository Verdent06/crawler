"""Optional model client. Uses an OpenAI-compatible chat API and falls back when unset."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class LlmClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        if base_url is None:
            self.base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        else:
            self.base_url = base_url.rstrip("/")
        if api_key is None:
            self.api_key = (
                os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
            )
        else:
            self.api_key = api_key
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self._client = client
        self._available: bool | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def available(self) -> bool:
        if not self.enabled:
            return False
        if self._available is not None:
            return self._available
        resp = self._request("GET", "/models", timeout=3.0)
        self._available = resp is not None and resp.status_code == 200
        return self._available

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response | None:
        if not self.enabled:
            return None
        client = self._client or httpx.Client()
        owns = self._client is None
        try:
            return client.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout if timeout is None else timeout,
                json=json_body,
            )
        except Exception:
            return None
        finally:
            if owns:
                client.close()

    def _chat_json(self, prompt: str) -> dict[str, Any] | None:
        if not self.available():
            return None
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
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
        resp = self._request("POST", "/chat/completions", json_body=payload)
        if resp is not None and resp.status_code == 400:
            payload.pop("response_format", None)
            resp = self._request("POST", "/chat/completions", json_body=payload)
        if resp is None or resp.status_code != 200:
            return None
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            return None
        return parse_json_loose(content)

    def rerank_link(
        self, *, url: str, anchor_text: str, context: str
    ) -> dict[str, Any] | None:
        prompt = (
            "Score this link for municipal/school finance relevance.\n"
            f"URL: {url}\n"
            f"Anchor: {anchor_text}\n"
            f"Context: {context[:500]}\n"
            'Return JSON: {"link_type":"document|contact|navigation",'
            '"result_score":0-100,"follow_score":0-100,"reason":"short"}\n'
            "follow_score is how soon the crawler should open this page. "
            "Use a high follow_score for finance departments, budgets, ACFRs, "
            "and staff who handle them. Use a low follow_score for everything else."
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
