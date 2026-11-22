from __future__ import annotations

import httpx
from pydantic import BaseModel

from uzh_crawler.config import ConfigError
from uzh_crawler.digest.prompt import system_prompt, user_prompt
from uzh_crawler.digest.schema import annotation_model, parse_annotation
from uzh_crawler.taxonomy import TagValidationError, Taxonomy


class OllamaAnnotator:
    """Local Ollama chat call with a closed JSON schema and thinking disabled."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        taxonomy: Taxonomy,
        *,
        model: str,
        ollama_url: str,
        temperature: float,
        num_ctx: int,
    ):
        self.client = client
        self.taxonomy = taxonomy
        self.model = model
        self.base = ollama_url.rstrip("/")
        self.temperature = temperature
        self.num_ctx = num_ctx

    async def prepare(self) -> None:
        try:
            response = await self.client.get(f"{self.base}/api/version")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ConfigError(f"Ollama injoignable à {self.base}: {exc}") from exc

    async def annotate(self, *, url: str, markdown: str, lang: str) -> BaseModel:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt(self.taxonomy, lang)},
                {"role": "user", "content": user_prompt(url, markdown)},
            ],
            "stream": False,
            "format": annotation_model(self.taxonomy).model_json_schema(),
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
            },
        }
        response = await self.client.post(f"{self.base}/api/chat", json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TagValidationError(f"Ollama HTTP error: {exc}") from exc
        body = response.json()
        if isinstance(body, dict) and body.get("error") and not body.get("message"):
            raise TagValidationError(str(body["error"]))
        message = body.get("message") if isinstance(body, dict) else None
        content = ""
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        if not content.strip():
            raise TagValidationError("empty model response")
        return parse_annotation(content, self.taxonomy)
