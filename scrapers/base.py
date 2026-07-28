import asyncio
import os
import random
import re
from pathlib import Path

import nodriver as uc

from config import DELAY_MIN, DELAY_MAX, CAPTCHA_THRESHOLD

SCRAPER_PROFILE = Path(__file__).parent.parent / "data" / "browser_profile"
CHROME_PROFILE = Path(os.environ.get(
    "CHROME_PROFILE",
    os.path.expanduser("~/AppData/Local/Google/Chrome/User Data"),
))


class BaseScraper:
    def __init__(self, db, use_chrome=False, headless=False):
        self.db = db
        self.browser = None
        self.page = None
        self.use_chrome = use_chrome
        self.headless = headless

    async def start_browser(self):
        if self.use_chrome:
            profile = CHROME_PROFILE
            print(f"  Using your Chrome profile: {profile}")
            print("  (Make sure Chrome is closed first!)\n")
        else:
            profile = SCRAPER_PROFILE
            profile.mkdir(parents=True, exist_ok=True)

        try:
            self.browser = await uc.start(
                headless=self.headless,
                user_data_dir=str(profile),
                sandbox=False,
            )
        except Exception as e:
            # Chrome's SingletonLock makes the second process fail with an
            # opaque connect error; say what actually went wrong.
            if (profile / "SingletonLock").exists() or "connect" in str(e).lower():
                raise RuntimeError(
                    f"Could not start Chrome with profile {profile}.\n"
                    "Another scraper is most likely already running against it "
                    "(only one process may use a profile at a time).\n"
                    "Close the other scraper, or pass use_chrome/a separate profile."
                ) from e
            raise

    async def stop_browser(self):
        if self.browser:
            browser = self.browser
            self.browser = None
            self.page = None
            browser.stop()

    def _is_blocked(self, content):
        lower = content.lower()
        if self._is_404(lower):
            return False
        return (
            len(content) < CAPTCHA_THRESHOLD
            or "just a moment" in lower[:2000]
            or "<title>just a moment</title>" in lower[:2000]
            or "captcha" in lower[:3000]
        )

    def _is_404(self, lower_content):
        indicators = [
            "page not found", "page isn't available", "gig not found",
            "this page is no longer available", "doesn't exist",
            "has been removed", "no longer available",
        ]
        head = lower_content[:3000]
        if any(ind in head for ind in indicators):
            return True
        if re.search(r'\b404\b.*(?:error|not found|page)', head):
            return True
        if '<title' in head and '404' in head.split('</title')[0] and 'not found' in head.split('</title')[0]:
            return True
        return False

    async def navigate(self, url, wait=8, max_retries=4):
        # Do NOT close self.page first. Tab.close() doesn't refresh
        # Browser._targets, so the next get() picks the dead target and raises
        # "Session with given id not found". get(new_tab=False) reuses the same
        # target anyway, so there is no tab to leak.
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
