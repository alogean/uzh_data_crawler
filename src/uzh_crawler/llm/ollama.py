from __future__ import annotations

import json
import re

import httpx

from uzh_crawler.config import ConfigError


class ModelOutputError(ValueError):
    """The local model answered with something that cannot be used."""


class OllamaChat:
    """Local Ollama chat calls with thinking disabled: closed JSON schema, or free text."""

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
        body = await self._chat(model, system, user, repeat_penalty=1.25, schema=schema)
        return load_json_object(_content(body))

    async def text(self, *, model: str, system: str, user: str) -> str:
        """One chat turn in free text. An answer cut at the length limit is refused."""
        body = await self._chat(model, system, user, repeat_penalty=1.0)
        content = _content(body)
        if body.get("done_reason") == "length":
            raise ModelOutputError("model output was cut at the length limit")
        return content

    async def _chat(
        self, model: str, system: str, user: str, *, repeat_penalty: float, schema: dict | None = None
    ) -> dict:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": 4096,
                "repeat_penalty": repeat_penalty,
            },
        }
        if schema is not None:
            payload["format"] = schema
        response = await self.client.post(
            f"{self.base}/api/chat",
            json=payload,
            timeout=httpx.Timeout(180.0, connect=10.0),
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ModelOutputError("unexpected model response")
        return body


def _content(body: dict) -> str:
    if body.get("error") and not body.get("message"):
        raise ModelOutputError(str(body["error"]))
    message = body.get("message")
    content = str(message.get("content") or "") if isinstance(message, dict) else ""
    if not content.strip():
        raise ModelOutputError("empty model response")
    return content


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
