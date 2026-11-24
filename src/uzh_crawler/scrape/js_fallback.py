from __future__ import annotations

import logging

from uzh_crawler.config import USER_AGENT

logger = logging.getLogger(__name__)


async def render_page(url: str) -> str | None:
    """One Playwright pass. Returns None when the browser is missing or the page fails."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright is not installed; JS fallback skipped for %s", url)
        return None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=USER_AGENT)
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                return await page.content()
            finally:
                await browser.close()
    except Exception as exc:
        logger.warning("JS fallback failed for %s: %s", url, exc)
        return None
