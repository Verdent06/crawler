"""Network-free tests for the OpenAI-compatible model client."""

from __future__ import annotations

import json

import httpx

from app.llm import LlmClient


def test_disabled_without_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = LlmClient()
    assert client.enabled is False
    assert client.available() is False


def test_empty_base_url_disables_client():
    client = LlmClient(base_url="", api_key="test-key")
    assert client.enabled is False


def test_rerank_link_reads_chat_completion():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o-mini"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = {
            "link_type": "navigation",
            "result_score": 72,
            "follow_score": 64,
            "reason": "finance department page",
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = LlmClient(
        base_url="https://api.example/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        client=http,
    )
    result = client.rerank_link(
        url="https://city.gov/finance",
        anchor_text="Business Office",
        context="",
    )
    http.close()
    assert result is not None
    assert result["follow_score"] == 64
    assert result["link_type"] == "navigation"
