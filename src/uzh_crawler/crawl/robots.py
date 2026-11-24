from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from uzh_crawler.crawl.limiter import HostRateLimiter

logger = logging.getLogger(__name__)


class RobotsCache:
    """robots.txt of each host, fetched once however many workers ask at the same time."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        user_agent: str,
        *,
        ignore: bool,
        limiter: HostRateLimiter,
    ):
        self.client = client
        self.user_agent = user_agent
        self.ignore = ignore
        self.limiter = limiter
        # One task per host: workers that arrive while it runs wait for the same answer.
        self._loading: dict[str, asyncio.Task[tuple[str | None, RobotFileParser | None]]] = {}

    async def allowed(self, url: str) -> bool:
        if self.ignore:
            return True
        _text, parser = await self._robots(url)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    async def text(self, url: str) -> str | None:
        """The robots.txt body of this URL's host, or None when there is none."""
        text, _parser = await self._robots(url)
        return text

    async def _robots(self, url: str) -> tuple[str | None, RobotFileParser | None]:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        task = self._loading.get(key)
        if task is None:
            task = asyncio.ensure_future(self._load(url))
            self._loading[key] = task
        return await asyncio.shield(task)

    async def _load(self, url: str) -> tuple[str | None, RobotFileParser | None]:
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        await self.limiter.wait(parts.hostname or "")
        try:
            response = await self.client.get(robots_url)
        except httpx.HTTPError as exc:
            logger.warning("robots.txt unreachable for %s (%s); allowing", parts.hostname, exc)
            return None, None
        if response.status_code == 404:
            return None, None
        if response.status_code >= 400:
            logger.warning(
                "robots.txt returned %s for %s; allowing",
                response.status_code,
                parts.hostname,
            )
            return None, None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return response.text, parser
