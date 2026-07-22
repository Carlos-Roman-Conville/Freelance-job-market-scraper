import asyncio
import os
import random
from pathlib import Path

import nodriver as uc

from config import DELAY_MIN, DELAY_MAX, CAPTCHA_THRESHOLD

SCRAPER_PROFILE = Path(__file__).parent.parent / "data" / "browser_profile"
CHROME_PROFILE = Path(os.environ.get(
    "CHROME_PROFILE",
    os.path.expanduser("~/AppData/Local/Google/Chrome/User Data"),
))


class BaseScraper:
    def __init__(self, db, use_chrome=False):
        self.db = db
        self.browser = None
        self.page = None
        self.use_chrome = use_chrome

    async def start_browser(self):
        if self.use_chrome:
            profile = CHROME_PROFILE
            print(f"  Using your Chrome profile: {profile}")
            print("  (Make sure Chrome is closed first!)\n")
        else:
            profile = SCRAPER_PROFILE
            profile.mkdir(parents=True, exist_ok=True)

        self.browser = await uc.start(
            headless=False,
            user_data_dir=str(profile),
        )

    async def stop_browser(self):
        if self.browser:
            self.browser.stop()

    def _is_blocked(self, content):
        lower = content.lower()
        return (
            len(content) < CAPTCHA_THRESHOLD
            or "just a moment" in lower[:2000]
            or "<title>just a moment</title>" in lower[:2000]
            or "captcha" in lower[:3000]
        )

    async def navigate(self, url, wait=8, max_retries=4):
        self.page = await self.browser.get(url)
        await self.page.sleep(wait)
        content = await self.page.get_content()

        retry = 0
        while self._is_blocked(content) and retry < max_retries:
            retry += 1
            wait_time = 10 + retry * 5
            print(f"  Cloudflare/CAPTCHA detected (attempt {retry}/{max_retries}), waiting {wait_time}s...")
            await self.page.sleep(wait_time)
            content = await self.page.get_content()

        return content

    async def random_delay(self):
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        await asyncio.sleep(delay)

    async def scrape(self, search_query, pages):
        raise NotImplementedError
