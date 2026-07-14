"""Minimal async client for xAI's OpenAI-compatible chat/completions API."""

from __future__ import annotations

import httpx


class GrokChat:
    def __init__(self, api_key: str, *, model: str = "grok-4.3",
                 base_url: str = "https://api.x.ai/v1") -> None:
        self._key = api_key
        self._model = model
        self._base = base_url.rstrip("/")

    async def complete(self, messages: list[dict], *, temperature: float = 0.4,
                       max_tokens: int = 600) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": self._model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
