import html as html_mod
import json
import re
from urllib.parse import urlencode

from scrapers.base import BaseScraper
from config import CAPTCHA_THRESHOLD


def _clean_html(text):
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_mod.unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


class FiverrScraper(BaseScraper):
    SEARCH_URL = "https://www.fiverr.com/search/gigs"

    def build_search_url(self, query, page):
        params = urlencode({"query": query, "page": str(page)})
        return f"{self.SEARCH_URL}?{params}"

    def _parse_gig_cards(self, content):
        gigs = []

        gig_ids = re.findall(r'data-gig-id="(\d+)(?:_\d+)?"', content)

        seen_ids = set()
        for gig_id in gig_ids:
            if gig_id in seen_ids:
                continue
            seen_ids.add(gig_id)

            start = content.find(f'data-gig-id="{gig_id}"')
            if start < 0:
                start = content.find(f'data-gig-id="{gig_id}_')
            if start < 0:
                continue

            next_card = content.find('data-gig-id="', start + 50)
            block = content[start:next_card] if next_card > 0 else content[start:start + 15000]

            url_match = re.search(r'<a\s+href="(/[^"?]+)', block)
            gig_url = f"https://www.fiverr.com{url_match.group(1)}" if url_match else ""

            text_blocks = []
            for m in re.finditer(r'>([^<]{2,})<', block):
                t = m.group(1).strip()
                if t and not t.startswith('{') and 'function' not in t and not t.startswith('//'):
                    text_blocks.append(t)

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

                if text.lower().startswith("i will ") or (len(text) > 30 and not title):
                    title = text
                elif re.match(r'^(Level \d|Top Rated|New Seller|Pro\b)', text, re.I):
                    seller_level = text
                elif re.match(r'^\d\.\d$', text):
                    try:
                        rating = float(text)
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
                elif re.match(r'^\$[\d,.]+$', text):
                    try:
                        price_min = float(text.replace("$", "").replace(",", ""))
                    except ValueError:
                        pass
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
                "num_reviews": num_reviews,
                "rating": rating,
            })

        return gigs

    def _parse_detail_page(self, content, card_data):
        """Extract rich data from a gig detail page using JSON-LD and page text."""
        data = {
            "gig_url": card_data.get("gig_url", ""),
            "title": card_data.get("title", ""),
            "description": "",
            "seller_name": card_data.get("seller_name", ""),
            "seller_level": card_data.get("seller_level", ""),
            "seller_country": "",
            "seller_languages": "",
            "seller_orders_completed": None,
            "price_min": card_data.get("price_min"),
            "price_max": None,
            "hourly_rate": None,
            "delivery_days": None,
            "num_reviews": card_data.get("num_reviews"),
            "rating": card_data.get("rating"),
            "category": "",
            "subcategory": "",
            "tags": [],
        }

        # --- JSON-LD extraction ---
        json_ld_blocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            content, re.DOTALL,
        )
        for block in json_ld_blocks:
            try:
                ld = json.loads(block)
            except (json.JSONDecodeError, ValueError):
                continue

            if isinstance(ld, list):
                for item in ld:
                    if isinstance(item, dict):
                        json_ld_blocks.append(json.dumps(item))
                continue

            if not isinstance(ld, dict):
                continue

            if ld.get("@type") == "BreadcrumbList":
                items = ld.get("itemListElement", [])
                if len(items) >= 2:
                    data["category"] = items[0].get("name", "")
                    data["subcategory"] = items[1].get("name", "")

            elif ld.get("@type") == "Product":
                offers = ld.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if not isinstance(offers, dict):
                    offers = {}
                low = offers.get("lowPrice")
                high = offers.get("highPrice")
                try:
                    if low is not None:
                        data["price_min"] = float(low)
                except (ValueError, TypeError):
                    pass
                try:
                    if high is not None:
                        data["price_max"] = float(high)
                except (ValueError, TypeError):
                    pass

                agg = ld.get("aggregateRating") or {}
                try:
                    if agg.get("ratingValue") is not None:
                        data["rating"] = float(agg["ratingValue"])
                except (ValueError, TypeError):
                    pass
                try:
                    if agg.get("reviewCount") is not None:
                        data["num_reviews"] = int(float(agg["reviewCount"]))
                except (ValueError, TypeError):
                    pass

                ld_name = ld.get("name", "")
                if ld_name:
                    if not ld_name.lower().startswith("i will"):
                        ld_name = "I will " + ld_name
                    data["title"] = ld_name

        # --- Visible text extraction ---
        # Strip all tags, decode entities, collapse whitespace
        text = re.sub(r'<script[^>]*>.*?</script>', ' ', content, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL)
        text = _clean_html(text)

        # Description from "About this Gig" / "About this gig"
        about_match = re.search(
            r'About this [Gg]ig\s*(.+?)'
            r'(?:Compare packages|About the Seller|What people loved|My Portfolio|'
            r'Get to know|\+ See More|Related tags)',
            text, re.DOTALL,
        )
        if about_match:
            desc = about_match.group(1).strip()
            desc = re.sub(r'\s*(Read More|Full Screen|View Presentation)\s*', ' ', desc)
            desc = re.sub(r'\s+', ' ', desc).strip()
            if len(desc) > 30:
                data["description"] = desc[:10000]

        # Delivery days
        delivery_match = re.search(r'(\d+)\s*-?\s*days?\s*delivery', text, re.I)
        if delivery_match:
            data["delivery_days"] = int(delivery_match.group(1))

        # Seller country
        country_match = re.search(
            r'From\s+'
            r'(Pakistan|India|United States|Bangladesh|Philippines|Nigeria|Kenya|Egypt|'
            r'Sri Lanka|Vietnam|Indonesia|Brazil|Ukraine|Turkey|Romania|China|'
            r'United Kingdom|Canada|Germany|France|Australia|Israel|Morocco|'
            r'Tunisia|Algeria|Jordan|Lebanon|Nepal|Myanmar|Thailand|Malaysia|'
            r'Singapore|South Korea|Japan|Mexico|Argentina|Colombia|Peru|Chile|'
            r'Poland|Italy|Spain|Netherlands|Sweden|Denmark|Norway|Finland|'
            r'Czech Republic|Hungary|Serbia|Bulgaria|Greece|Portugal|Iran|'
            r'Russia|South Africa|Ghana|Cameroon|Tanzania|Uganda|Ethiopia|'
            r'Saudi Arabia|UAE|Qatar|Kuwait|Oman|Bahrain|Iraq|Afghanistan|'
            r'New Zealand|Ireland|Belgium|Austria|Switzerland|Croatia|'
            r'Slovakia|Slovenia|Bosnia|North Macedonia|Albania|Montenegro|'
            r'Latvia|Lithuania|Estonia|Georgia|Armenia|Azerbaijan|'
            r'Moldova|Belarus|Kazakhstan|Uzbekistan|Kyrgyzstan|'
            r'Venezuela|Ecuador|Bolivia|Paraguay|Uruguay|'
            r'Costa Rica|Panama|Honduras|Guatemala|El Salvador|Nicaragua|'
            r'Dominican Republic|Jamaica|Trinidad|Haiti|Cuba)',
            text, re.I,
        )
        if country_match and not data.get("seller_country"):
            data["seller_country"] = country_match.group(1).strip()

        # Seller languages
        lang_match = re.search(r'I speak\s+(.+?)(?:\d[\d,]*\s*orders?\s*completed|Contact\s)', text, re.I)
        if not lang_match:
            lang_match = re.search(r'Languages?\s+(.{3,200}?)(?:\s[A-Z][a-z]+ (?:patel|belongs|Member)|Avg\.|Last delivery|Member since|$)', text)
        if lang_match:
            raw_lang = lang_match.group(1).strip().rstrip(',. ')
            raw_lang = re.split(r'(?:Avg\.|Last delivery|Member since)', raw_lang)[0].strip().rstrip(',. ')
            data["seller_languages"] = raw_lang

        # Orders completed
        orders_match = re.search(r'([\d,]+)\s*orders?\s*completed', text, re.I)
        if orders_match:
            data["seller_orders_completed"] = int(orders_match.group(1).replace(",", ""))

        # Hourly rate
        hourly_match = re.search(r'\$([\d,.]+)\s*/\s*hour', text, re.I)
        if hourly_match:
            data["hourly_rate"] = float(hourly_match.group(1).replace(",", ""))

        # Seller level from detail (only if card data is empty)
        if not data.get("seller_level"):
            level_match = re.search(r'(Level [12]|Top Rated Seller|Pro Verified)\s', text, re.I)
            if level_match:
                data["seller_level"] = level_match.group(1).strip()

        # Tags: extract from raw HTML
        noise = {
            "read more", "see all", "contact me", "vetted for",
            "technology:", "information type:", "technique:", "type:",
            "level 1", "level 2", "top rated", "pro verified",
            "buyers keep", "returning", "full screen", "see more",
            "helpful", "yes", "no", "show more reviews",
            "sort by", "most relevant", "related tags",
        }

        # Method 1: Data-category style — skill spans between Read More and reviews
        skills_section = re.search(
            r'Read More</a>(.+?)(?:Buyers keep|What people loved)',
            content, re.DOTALL,
        )
        if skills_section:
            tag_texts = re.findall(
                r'data-track-tag="text"[^>]*>([^<]+)<', skills_section.group(1)
            )
            if not tag_texts:
                tag_texts = re.findall(r'>([^<]{3,50})<', skills_section.group(1))
            tags = [t.strip() for t in tag_texts
                    if 2 < len(t.strip()) < 50
                    and not re.match(r'^\+\d+$', t.strip())
                    and t.strip().lower() not in noise]
            data["tags"] = list(dict.fromkeys(tags))[:20]

        # Method 2: "Related tags" section from raw HTML (li > a elements)
        if not data["tags"]:
            related_section = re.search(
                r'Related tags</h2>\s*<ul>(.*?)</ul>',
                content, re.DOTALL,
            )
            if related_section:
                tag_texts = re.findall(r'<a[^>]*>([^<]+)</a>', related_section.group(1))
                tags = [t.strip() for t in tag_texts
                        if 2 < len(t.strip()) < 50
                        and t.strip().lower() not in noise]
                data["tags"] = list(dict.fromkeys(tags))[:20]

        # Method 3: Seller skills from raw HTML (near "Skills" heading)
        if not data["tags"]:
            skills_html = re.search(
                r'Skills</[^>]+>(.+?)(?:\+\d+|More about|Get to know|Compare)',
                content, re.DOTALL,
            )
            if skills_html:
                tag_texts = re.findall(r'>([^<]{3,50})<', skills_html.group(1))
                tags = [t.strip() for t in tag_texts
                        if 2 < len(t.strip()) < 50
                        and not re.match(r'^\+\d+$', t.strip())
                        and t.strip().lower() not in noise]
                data["tags"] = list(dict.fromkeys(tags))[:20]

        return data

    async def scrape_query(self, search_query, pages):
        """Scrape a single query. Caller must ensure browser is already started."""
        run_id = self.db.start_run("fiverr", search_query)
        total = 0
        pages_done = 0

        try:
            for page_num in range(1, pages + 1):
                url = self.build_search_url(search_query, page_num)
                print(f"\n[Page {page_num}/{pages}] {url}")
                content = await self.navigate(url)

                print(f"  Content length: {len(content)} bytes")

                if self._is_blocked(content):
                    print("  Page is blocked. Stopping.")
                    break

                cards = self._parse_gig_cards(content)
                print(f"  Found {len(cards)} gigs, visiting each for details...")

                for i, card in enumerate(cards):
                    gig_url = card["gig_url"]
                    if not gig_url:
                        print(f"  [{i+1}/{len(cards)}] Skipping — no URL")
                        continue
                    short_title = card.get("title", "?")[:50]
                    print(f"  [{i+1}/{len(cards)}] {short_title}")

                    await self.random_delay()

                    try:
                        detail_content = await self.navigate(gig_url, wait=10)

                        if self._is_blocked(detail_content):
                            print(f"    BLOCKED — using card data only")
                            card["description"] = ""
                            card["seller_country"] = ""
                            card["seller_languages"] = ""
                            card["seller_orders_completed"] = None
                            card["price_max"] = None
                            card["hourly_rate"] = None
                            card["delivery_days"] = None
                            card["category"] = ""
                            card["subcategory"] = ""
                            card["tags"] = []
                            card["search_query"] = search_query
                            if self.db.insert_fiverr_gig(run_id, card):
                                total += 1
                            continue

                        if self._is_404(detail_content.lower()):
                            print(f"    404 — gig removed, skipping")
                            continue

                        gig_data = self._parse_detail_page(detail_content, card)
                        gig_data["search_query"] = search_query

                        if self.db.insert_fiverr_gig(run_id, gig_data):
                            total += 1
                            cat = gig_data.get("category", "")
                            country = gig_data.get("seller_country", "?")
                            price = f"${gig_data['price_min']}" if gig_data.get("price_min") is not None else "N/A"
                            if gig_data.get("price_max"):
                                price += f"-${gig_data['price_max']}"
                            desc_len = len(gig_data.get("description", ""))
                            print(f"    + {cat} | {country} | {price} | {desc_len} chars")
                        else:
                            print(f"    = already in DB")

                    except Exception as e:
                        print(f"    ERROR: {e}")
                        card["description"] = ""
                        card["seller_country"] = ""
                        card["seller_languages"] = ""
                        card["seller_orders_completed"] = None
                        card["price_max"] = None
                        card["hourly_rate"] = None
                        card["delivery_days"] = None
                        card["category"] = ""
                        card["subcategory"] = ""
                        card["tags"] = []
                        card["search_query"] = search_query
                        if self.db.insert_fiverr_gig(run_id, card):
                            total += 1
                            print(f"    + saved card data only")

                pages_done = page_num

                if not cards:
                    print("  No gigs found, stopping pagination.")
                    break

                if page_num < pages:
                    await self.random_delay()
        finally:
            self.db.finish_run(run_id, pages_done, total)

        return total

    async def scrape(self, search_query, pages):
        """Scrape a single query with automatic browser lifecycle."""
        await self.start_browser()
        try:
            return await self.scrape_query(search_query, pages)
        finally:
            await self.stop_browser()
