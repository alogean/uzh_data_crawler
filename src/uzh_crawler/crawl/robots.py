from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from uzh_crawler.crawl.limiter import HostRateLimiter

logger = logging.getLogger(__name__)


class RobotsCache:
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
        self._cache: dict[str, RobotFileParser | None] = {}

    async def allowed(self, url: str) -> bool:
        if self.ignore:
            return True
        origin = urlsplit(url)
        host = origin.hostname or ""
        if host not in self._cache:
            self._cache[host] = await self._load(url)
        parser = self._cache[host]
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    async def _load(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        await self.limiter.wait(parts.hostname or "")
        try:
            response = await self.client.get(robots_url)
        except httpx.HTTPError as exc:
            logger.warning("robots.txt unreachable for %s (%s); allowing", parts.hostname, exc)
            return None
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            logger.warning(
                "robots.txt returned %s for %s; allowing",
                response.status_code,
                parts.hostname,
            )
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser
