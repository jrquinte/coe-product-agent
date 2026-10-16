"""
Browser-based HTML fetcher using Playwright.

Lazy-imports playwright so it is only loaded when use_browser_fallback=True.
The browser is initialized once and reused across all fetches within a run.
"""
import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


class BrowserFetcher:
    """Singleton-per-run Playwright browser. Call close() when done."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None

    async def _ensure_ready(self) -> None:
        if self._context is not None:
            return
        # Lazy import — only pulled in when browser is actually needed
        from playwright.async_api import async_playwright

        logger.info("Initializing Playwright browser (reused for this run)")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._context = await self._browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
        )
        # Mask the webdriver property that bot detectors check
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    async def fetch(self, url: str, wait_selector: Optional[str] = None) -> Optional[str]:
        """Fetch a page with JS rendering and basic human simulation."""
        await self._ensure_ready()
        page = None
        try:
            page = await self._context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Human-like mouse movements and scroll to trigger lazy loading
            await page.wait_for_timeout(random.randint(800, 1400))
            for _ in range(4):
                x, y = random.randint(200, 1700), random.randint(200, 800)
                await page.mouse.move(x, y, steps=random.randint(5, 10))
                await page.wait_for_timeout(random.randint(100, 250))
            for scroll_y in [300, 600, 900, 600, 300, 0]:
                await page.evaluate(f"window.scrollTo(0, {scroll_y})")
                await page.wait_for_timeout(random.randint(300, 600))

            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=5000)
                except Exception:
                    pass

            html = await page.content()
            logger.info(f"Browser fetch OK: {url} ({len(html):,} chars)")
            return html
        except Exception as e:
            logger.error(f"Browser fetch failed for {url}: {e}")
            return None
        finally:
            if page:
                await page.close()

    async def close(self) -> None:
        """Release Playwright resources."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.debug(f"Browser close error (non-fatal): {e}")
        finally:
            self._context = self._browser = self._playwright = None
