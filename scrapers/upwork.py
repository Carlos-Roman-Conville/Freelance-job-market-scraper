import json
import re
from urllib.parse import urlencode

from scrapers.base import BaseScraper
from config import CAPTCHA_THRESHOLD


def _safe(pattern, content, group=1, default=""):
    match = re.search(pattern, content, re.DOTALL)
    return match.group(group).strip() if match else default


def _clean_html(text):
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


class UpworkScraper(BaseScraper):
    SEARCH_URL = "https://www.upwork.com/nx/search/jobs/"

    def build_search_url(self, query, page):
        params = urlencode({"q": query, "page": str(page), "per_page": "10"})
        return f"{self.SEARCH_URL}?{params}"

    def _extract_job_urls_from_search(self, content):
        """Pull job URLs and basic card data from the search results page."""
        cards = []

        tile_keys = [k for k in re.findall(r'data-test-key="(\d+)"', content) if len(k) > 10]
        if not tile_keys:
            return cards

        for key in tile_keys:
            start_pos = content.find(f'data-test-key="{key}"')
            if start_pos < 0:
                continue

            next_pos = len(content)
            for other_key in tile_keys:
                if other_key != key:
                    other_pos = content.find(f'data-test-key="{other_key}"', start_pos + 100)
                    if 0 < other_pos < next_pos:
                        next_pos = other_pos

            block = content[start_pos:next_pos]

            url_match = re.search(r'href="(/jobs/[^"?]+)', block)
            job_url = f"https://www.upwork.com{url_match.group(1)}" if url_match else ""

            title_match = re.search(r'data-test="job-tile-title-link[^"]*"[^>]*>(.*?)</a>', block, re.DOTALL)
            title = _clean_html(title_match.group(1)) if title_match else ""

            if not title or not job_url:
                continue

            # Grab what we can from the card as a starting point
            posted_match = re.search(r'data-test="job-pubilshed-date"[^>]*>.*?<span>([^<]+)', block, re.DOTALL)
            posted_date = posted_match.group(1).strip() if posted_match else ""

            type_match = re.search(r'data-test="job-type-label"[^>]*>.*?<strong>([^<]+)', block, re.DOTALL)
            type_text = _clean_html(type_match.group(1)) if type_match else ""

            budget_type = ""
            budget_min = None
            budget_max = None
            if "hourly" in type_text.lower():
                budget_type = "Hourly"
                amounts = re.findall(r'\$([\d,.]+)', type_text)
                if len(amounts) >= 2:
                    budget_min = float(amounts[0].replace(",", ""))
                    budget_max = float(amounts[1].replace(",", ""))
                elif len(amounts) == 1:
                    budget_min = float(amounts[0].replace(",", ""))
            elif "fixed" in type_text.lower() or re.search(r'\$[\d,]+', type_text):
                budget_type = "Fixed"
                amounts = re.findall(r'\$([\d,.]+)', type_text)
                if amounts:
                    budget_min = float(amounts[0].replace(",", ""))

            if not budget_type:
                fixed_match = re.search(r'data-test="is-fixed-price"[^>]*>.*?<strong>([^<]+)', block, re.DOTALL)
                if fixed_match:
                    budget_type = "Fixed"
                    amounts = re.findall(r'\$([\d,.]+)', fixed_match.group(1))
                    if amounts:
                        budget_min = float(amounts[0].replace(",", ""))

            exp_match = re.search(r'data-test="experience-level"[^>]*>.*?<strong>([^<]+)', block, re.DOTALL)
            experience = _clean_html(exp_match.group(1)) if exp_match else ""

            skills = []
            skill_matches = re.findall(r'data-test="token"[^>]*>.*?<span[^>]*>([^<]+)', block, re.DOTALL)
            skills = [_clean_html(s) for s in skill_matches if len(s.strip()) > 1]

            cards.append({
                "job_url": job_url,
                "title": title,
                "skills": skills,
                "budget_type": budget_type,
                "budget_min": budget_min,
                "budget_max": budget_max,
                "experience_level": experience,
                "posted_date": posted_date,
            })

        return cards

    def _parse_job_detail(self, content, card_data):
        """Parse a full job detail page. Merges with card_data from search results."""
        data = {
            "job_url": card_data.get("job_url", ""),
            "title": card_data.get("title", ""),
            "description": "",
            "skills": card_data.get("skills", []),
            "budget_type": card_data.get("budget_type", ""),
            "budget_min": card_data.get("budget_min"),
            "budget_max": card_data.get("budget_max"),
            "experience_level": card_data.get("experience_level", ""),
            "client_rating": None,
            "client_total_spent": "",
            "client_hire_rate": "",
            "client_country": "",
            "num_proposals": None,
            "posted_date": card_data.get("posted_date", ""),
            "category": "",
        }

        # --- Full description ---
        # Try multiple selectors for the job description section
        for desc_pattern in [
            r'data-test="Description"[^>]*>(.*?)(?=</section|<section|<div[^>]*data-test="(?:Skills|Activity|About)',
            r'class="[^"]*job-description[^"]*"[^>]*>(.*?)(?=</section|<section)',
            r'class="[^"]*description[^"]*"[^>]*>(.*?)(?=</div>\s*</div>\s*<(?:section|div[^>]*data-test))',
            r'<div[^>]*data-test="PostedBy"[^>]*>.*?</div>\s*(.*?)(?=<section|<div[^>]*(?:data-test="Skills|class="sidebar"))',
        ]:
            desc_match = re.search(desc_pattern, content, re.DOTALL | re.I)
            if desc_match:
                raw = desc_match.group(1)
                cleaned = _clean_html(raw)
                if len(cleaned) > 50:
                    data["description"] = cleaned[:10000]
                    break

        # If still no description, grab the biggest text block on the page
        if not data["description"]:
            text_blocks = re.findall(r'<(?:p|div)[^>]*>(.*?)</(?:p|div)>', content, re.DOTALL)
            longest = ""
            for block in text_blocks:
                cleaned = _clean_html(block)
                if len(cleaned) > len(longest) and len(cleaned) > 100:
                    longest = cleaned
            if longest:
                data["description"] = longest[:10000]

        # --- Title from detail page (more reliable than card) ---
        title_match = re.search(r'<h1[^>]*>(.*?)</h1>', content, re.DOTALL)
        if title_match:
            detail_title = _clean_html(title_match.group(1))
            if detail_title:
                data["title"] = detail_title

        # --- Skills from detail page (may have more than card) ---
        detail_skills = []
        skills_section = re.search(r'data-test="Skills"(.*?)(?=</section|<section)', content, re.DOTALL | re.I)
        if not skills_section:
            skills_section = re.search(r'class="[^"]*skills[^"]*"(.*?)(?=</section|</div>\s*</div>\s*<section)', content, re.DOTALL | re.I)
        if skills_section:
            skill_items = re.findall(r'>([^<]{2,30})<', skills_section.group(1))
            detail_skills = [_clean_html(s) for s in skill_items
                           if len(s.strip()) > 1
                           and s.strip().lower() not in ("skills", "skills and expertise", "other skills")]
        if detail_skills:
            data["skills"] = detail_skills

        # --- Client info ---
        # Look for the client/about section
        client_section = re.search(
            r'(?:data-test="(?:AboutClient|ClientInfo|client-info)"|class="[^"]*client[^"]*sidebar[^"]*"|About the client)(.*?)(?=</section|<section|$)',
            content, re.DOTALL | re.I
        )
        client_block = client_section.group(1) if client_section else content

        # Payment verified
        # Rating
        rating_match = re.search(r'([\d.]+)\s*(?:of 5|stars?|★)', client_block, re.I)
        if not rating_match:
            rating_match = re.search(r'(?:Rating|rating)[^>]*>.*?([\d.]+)', client_block, re.DOTALL)
        if rating_match:
            try:
                data["client_rating"] = float(rating_match.group(1))
            except ValueError:
                pass

        # Total spent
        spent_match = re.search(r'\$([\d,.]+[KkMm]?)\s*(?:total\s*)?spent', client_block, re.I)
        if not spent_match:
            spent_match = re.search(r'(?:spent|Total spent)[^>]*>\s*\$?([\d,.]+[KkMm]?)', client_block, re.DOTALL | re.I)
        if spent_match:
            data["client_total_spent"] = spent_match.group(1).strip()

        # Hire rate
        hire_match = re.search(r'(\d+)%?\s*(?:hire\s*rate)', client_block, re.I)
        if hire_match:
            data["client_hire_rate"] = f"{hire_match.group(1)}%"

        # Country
        country_match = re.search(r'(?:Member since|Location)[^>]*>.*?<[^>]*>([A-Z][a-zA-Z\s]{2,25})<', client_block, re.DOTALL)
        if not country_match:
            country_match = re.search(r'data-test="(?:client-location|ClientLocation)"[^>]*>([^<]+)', client_block)
        if country_match:
            data["client_country"] = country_match.group(1).strip()

        # --- Proposals ---
        proposals_match = re.search(r'(\d+)\s+(?:to\s+\d+\s+)?proposals?', content, re.I)
        if not proposals_match:
            proposals_match = re.search(r'(?:Proposals|proposals)[^>]*>\s*(\d+)', content, re.DOTALL)
        if proposals_match:
            try:
                data["num_proposals"] = int(proposals_match.group(1))
            except ValueError:
                pass

        # --- Category ---
        cat_match = re.search(r'(?:data-test="Category"|class="[^"]*category[^"]*")[^>]*>([^<]+)', content, re.I)
        if not cat_match:
            # Try breadcrumb
            cat_match = re.search(r'<a[^>]*href="/cat/[^"]*"[^>]*>([^<]+)', content)
        if cat_match:
            data["category"] = _clean_html(cat_match.group(1))

        # --- Budget from detail page (override card if found) ---
        if not data["budget_type"]:
            if re.search(r'Fixed.price|Fixed-Price', content, re.I):
                data["budget_type"] = "Fixed"
            elif re.search(r'Hourly', content, re.I):
                data["budget_type"] = "Hourly"

        if not data["budget_min"]:
            budget_match = re.search(r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:-\s*\$\s*([\d,]+(?:\.\d+)?))?', content)
            if budget_match:
                data["budget_min"] = float(budget_match.group(1).replace(",", ""))
                if budget_match.group(2):
                    data["budget_max"] = float(budget_match.group(2).replace(",", ""))

        # --- Posted date from detail ---
        if not data["posted_date"]:
            posted = _safe(r'(?:Posted|posted)\s+([\w\s]+ago)', content)
            if posted:
                data["posted_date"] = posted

        return data

    async def scrape(self, search_query, pages):
        run_id = self.db.start_run("upwork", search_query)
        await self.start_browser()
        total = 0

        try:
            print("Navigating to Upwork homepage...")
            content = await self.navigate("https://www.upwork.com", wait=10)
            if self._is_blocked(content):
                print("\n  Cloudflare is blocking access.")
                print("  Try running with --chrome flag to use your logged-in Chrome profile.")
                print("  (Close Chrome first, then run:)")
                print(f"  python scrape_jobs.py upwork --search \"{search_query}\" --chrome\n")

            for page_num in range(1, pages + 1):
                url = self.build_search_url(search_query, page_num)
                print(f"\n[Page {page_num}/{pages}] {url}")
                content = await self.navigate(url)

                print(f"  Content length: {len(content)} bytes")

                if self._is_blocked(content):
                    print("  Page is blocked by Cloudflare. Stopping.")
                    break

                # Phase 1: Get job URLs and card data from search results
                cards = self._extract_job_urls_from_search(content)
                print(f"  Found {len(cards)} jobs, visiting each for full details...")

                # Phase 2: Visit each job page for full description + client info
                for i, card in enumerate(cards):
                    job_url = card["job_url"]
                    print(f"  [{i+1}/{len(cards)}] {card['title'][:55]}")

                    await self.random_delay()

                    try:
                        detail_content = await self.navigate(job_url)

                        if self._is_blocked(detail_content):
                            print(f"    BLOCKED — using card data only")
                            card["description"] = ""
                            card["client_rating"] = None
                            card["client_total_spent"] = ""
                            card["client_hire_rate"] = ""
                            card["client_country"] = ""
                            card["num_proposals"] = None
                            card["category"] = ""
                            card["search_query"] = search_query
                            if self.db.insert_upwork_job(run_id, card):
                                total += 1
                            continue

                        job_data = self._parse_job_detail(detail_content, card)
                        job_data["search_query"] = search_query

                        desc_len = len(job_data.get("description", ""))
                        client = job_data.get("client_country", "") or "?"
                        budget = ""
                        if job_data.get("budget_min"):
                            budget = f"${job_data['budget_min']}"
                            if job_data.get("budget_max"):
                                budget += f"-${job_data['budget_max']}"

                        if self.db.insert_upwork_job(run_id, job_data):
                            total += 1
                            print(f"    + {desc_len} chars | {budget} | {client}")
                        else:
                            print(f"    = already in DB")

                    except Exception as e:
                        print(f"    ERROR: {e}")

                if not cards:
                    print("  No jobs found on this page.")
                    break

                if page_num < pages:
                    await self.random_delay()

        finally:
            await self.stop_browser()
            self.db.finish_run(run_id, pages, total)

        return total
