from __future__ import annotations

import json
import re

import httpx

from uzh_crawler.config import ConfigError


class ModelOutputError(ValueError):
    """The local model answered with something that cannot be used."""


class OllamaChat:
    """Local Ollama chat call with a closed JSON schema and thinking disabled."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        ollama_url: str,
        temperature: float,
        num_ctx: int,
    ):
        self.client = client
        self.base = ollama_url.rstrip("/")
        self.temperature = temperature
        self.num_ctx = num_ctx

    async def prepare(self) -> None:
        try:
            response = await self.client.get(f"{self.base}/api/version")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ConfigError(f"Ollama injoignable à {self.base}: {exc}") from exc

    async def json_object(self, *, model: str, system: str, user: str, schema: dict) -> dict:
        """One chat turn. Returns the JSON object the model wrote."""
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": 4096,
                "repeat_penalty": 1.25,
            },
        }
        response = await self.client.post(
            f"{self.base}/api/chat",
            json=payload,
            timeout=httpx.Timeout(180.0, connect=10.0),
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and body.get("error") and not body.get("message"):
            raise ModelOutputError(str(body["error"]))
        message = body.get("message") if isinstance(body, dict) else None
        content = str(message.get("content") or "") if isinstance(message, dict) else ""
        if not content.strip():
            raise ModelOutputError("empty model response")
        return load_json_object(content)


def load_json_object(payload: str) -> dict:
    """Parse a model answer, tolerating a code fence or text around the object."""
    text = payload.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ModelOutputError("model output is not JSON") from exc
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as inner:
            raise ModelOutputError("model output is not JSON") from inner
    if not isinstance(data, dict):
        raise ModelOutputError("model output is not a JSON object")
    return data
