import json
import re
from urllib.parse import urlencode

from scrapers.base import BaseScraper
from config import CAPTCHA_THRESHOLD


def _clean_html(text):
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


class FiverrScraper(BaseScraper):
    SEARCH_URL = "https://www.fiverr.com/search/gigs"

    def build_search_url(self, query, page):
        params = urlencode({"query": query, "page": str(page)})
        return f"{self.SEARCH_URL}?{params}"

    def _parse_gig_cards(self, content):
        gigs = []

        # Split by data-gig-id to get individual cards
        gig_ids = re.findall(r'data-gig-id="(\d+)_\d+"', content)
        if not gig_ids:
            gig_ids = re.findall(r'data-gig-id="(\d+)"', content)

        seen_ids = set()
        for gig_id in gig_ids:
            if gig_id in seen_ids:
                continue
            seen_ids.add(gig_id)

            # Find this card's block
            start = content.find(f'data-gig-id="{gig_id}')
            if start < 0:
                continue

            # Find next card or end
            next_card = content.find('data-gig-id="', start + 50)
            block = content[start:next_card] if next_card > 0 else content[start:start + 15000]

            # Extract URL from first <a href="...">
            url_match = re.search(r'<a\s+href="(/[^"?]+)', block)
            gig_url = f"https://www.fiverr.com{url_match.group(1)}" if url_match else ""

            # Extract all visible text blocks in order
            text_blocks = []
            for m in re.finditer(r'>([^<]{2,})<', block):
                t = m.group(1).strip()
                if t and not t.startswith('{') and 'function' not in t and not t.startswith('//'):
                    text_blocks.append(t)

            # Parse structured text:
            # Pattern: seller_name, level (optional), title (starts with "I will"),
            #          rating (number like "4.8"), reviews ("1k+", "(123)"), "From", price ("$30")
            seller_name = ""
            seller_level = ""
            title = ""
            rating = None
            num_reviews = None
            price_min = None

            for i, text in enumerate(text_blocks):
                text = text.replace("&nbsp;", " ").replace("&amp;", "&").strip()

                if not text:
                    continue

                # Title: usually starts with "I will" or is a long descriptive phrase
                if text.lower().startswith("i will ") or (len(text) > 30 and not title):
                    title = text

                # Level
                elif re.match(r'^(Level \d|Top Rated|New Seller|Pro)', text, re.I):
                    seller_level = text

                # Rating: standalone decimal number like "4.8", "5.0"
                elif re.match(r'^\d\.\d$', text):
                    try:
                        rating = float(text)
                    except ValueError:
                        pass

                # Review count: "1k+", "(123)", "23"
                elif re.match(r'^[\d,]+k?\+?$', text, re.I) and not title:
                    rev_text = text.replace(",", "").replace("+", "").lower()
                    if "k" in rev_text:
                        num_reviews = int(float(rev_text.replace("k", "")) * 1000)
                    else:
                        try:
                            num_reviews = int(rev_text)
                        except ValueError:
                            pass
                elif re.match(r'^[\d,]+k?\+?$', text, re.I) and title and num_reviews is None:
                    rev_text = text.replace(",", "").replace("+", "").lower()
                    if "k" in rev_text:
                        num_reviews = int(float(rev_text.replace("k", "")) * 1000)
                    else:
                        try:
                            val = int(rev_text)
                            if val < 100000:
                                num_reviews = val
                        except ValueError:
                            pass

                # Price: "$30", "$125"
                elif re.match(r'^\$[\d,.]+$', text):
                    try:
                        price_min = float(text.replace("$", "").replace(",", ""))
                    except ValueError:
                        pass

                # Seller name: first short text block that isn't a level or keyword
                elif (not seller_name
                      and not title
                      and len(text) < 40
                      and text not in ("From", "Promoted", "Ad")
                      and not re.match(r'^[\d.$,]+', text)):
                    seller_name = text

            if not title:
                continue

            gigs.append({
                "gig_url": gig_url,
                "title": title,
                "seller_name": seller_name,
                "seller_level": seller_level,
                "price_min": price_min,
                "price_max": None,
                "num_reviews": num_reviews,
                "rating": rating,
                "category": "",
                "tags": [],
            })

        return gigs

    async def scrape(self, search_query, pages):
        run_id = self.db.start_run("fiverr", search_query)
        await self.start_browser()
        total = 0

        try:
            for page_num in range(1, pages + 1):
                url = self.build_search_url(search_query, page_num)
                print(f"\n[Page {page_num}/{pages}] {url}")
                content = await self.navigate(url)

                print(f"  Content length: {len(content)} bytes")

                if self._is_blocked(content):
                    print("  Page is blocked. Stopping.")
                    break

                gigs = self._parse_gig_cards(content)
                print(f"  Found {len(gigs)} gigs")

                for gig in gigs:
                    gig["search_query"] = search_query
                    if self.db.insert_fiverr_gig(run_id, gig):
                        total += 1
                        price = f"${gig['price_min']}" if gig.get("price_min") else "?"
                        rating = f" | {gig['rating']}*" if gig.get("rating") else ""
                        print(f"    + {gig.get('title', '?')[:50]} | {price}{rating}")
                    else:
                        print(f"    = {gig.get('title', '?')[:50]} (dupe)")

                if not gigs:
                    print("  No gigs found, stopping pagination.")
                    break

                if page_num < pages:
                    await self.random_delay()

        finally:
            await self.stop_browser()
            self.db.finish_run(run_id, pages, total)

        return total
