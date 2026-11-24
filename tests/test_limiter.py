import asyncio
import time

from uzh_crawler.crawl.limiter import HostRateLimiter


def _elapsed(limiter: HostRateLimiter, host: str, requests: int) -> float:
    async def scenario() -> float:
        started = time.monotonic()
        await asyncio.gather(*(limiter.wait(host) for _ in range(requests)))
        return time.monotonic() - started

    return asyncio.run(scenario())


def test_a_burst_passes_at_once_and_the_rest_follows_the_rate():
    limiter = HostRateLimiter(per_second=20, burst=5)
    assert _elapsed(limiter, "a.uzh.ch", 5) < 0.04
    # The bucket is empty: three more requests take three intervals of 50 ms.
    waited = _elapsed(limiter, "a.uzh.ch", 3)
    assert 0.12 < waited < 0.25
    # Another host has its own bucket.
    assert _elapsed(limiter, "b.uzh.ch", 5) < 0.04


def test_without_a_burst_requests_are_spaced_and_a_zero_rate_never_waits():
    assert _elapsed(HostRateLimiter(per_second=20), "a.uzh.ch", 3) > 0.09
    assert _elapsed(HostRateLimiter(per_second=0), "a.uzh.ch", 50) < 0.04


def test_robots_txt_is_fetched_once_when_many_workers_ask_together():
    import httpx

    from uzh_crawler.crawl.robots import RobotsCache

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /intern\nSitemap: /sitemap.txt\n")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            cache = RobotsCache(client, "bot", ignore=False, limiter=HostRateLimiter(0))
            urls = [f"https://www.phil.uzh.ch/de/page{n}.html" for n in range(20)]
            answers = await asyncio.gather(*(cache.allowed(url) for url in urls))
            blocked = await cache.allowed("https://www.phil.uzh.ch/intern/a.html")
            text = await cache.text("https://www.phil.uzh.ch/")
            return answers, blocked, text

    answers, blocked, text = asyncio.run(scenario())
    assert all(answers) and blocked is False
    assert "Sitemap: /sitemap.txt" in text
    assert calls == ["/robots.txt"]
