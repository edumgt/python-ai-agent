import httpx
import json
from typing import Any
from app.config import settings


class OllamaClient:
    def __init__(self, base_url: str, timeout: float):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def chat(self, model: str, messages: list[dict], options: dict | None = None) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]

    async def embed(self, model: str, input_text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/api/embed",
                json={"model": model, "input": input_text},
            )
            resp.raise_for_status()
            data = resp.json()
            emb = data.get("embeddings", [data.get("embedding", [])])
            return emb[0] if emb else []


_ollama: OllamaClient | None = None


def get_ollama() -> OllamaClient:
    global _ollama
    if _ollama is None:
        _ollama = OllamaClient(settings.OLLAMA_BASE_URL, settings.OLLAMA_TIMEOUT)
    return _ollama
