"""
Upwork job scraper using Upwork's internal GraphQL API + curl_cffi.

Uses TLS fingerprint impersonation (curl_cffi) to bypass Cloudflare,
fetches a visitor_gql_token from the homepage, then queries the
GraphQL endpoint for job listings. No browser needed.
"""

import json
import random
import re
import time

from curl_cffi import requests as cffi_requests

from config import DELAY_MIN, DELAY_MAX

GRAPHQL_URL = "https://www.upwork.com/api/graphql/v1"
TOKEN_COOKIE = "visitor_gql_token"
PAGE_SIZE = 50

JOB_SEARCH_QUERY = """
query VisitorJobSearch($requestVariables: VisitorJobSearchV1Request!) {
  search {
    universalSearchNuxt {
      visitorJobSearchV1(request: $requestVariables) {
        paging {
          total
          offset
          count
        }
        results {
          id
          title
          description
          ontologySkills {
            prefLabel
          }
          jobTile {
            job {
              id
              ciphertext: cipherText
              jobType
              hourlyBudgetMax
              hourlyBudgetMin
              contractorTier
              publishTime
              hourlyEngagementDuration {
                weeks
              }
              fixedPriceAmount {
                amount
              }
              fixedPriceEngagementDuration {
                weeks
              }
            }
          }
        }
      }
    }
  }
}
"""


def _clean_highlight(text):
    """Remove H^...^H highlight markers from search results."""
    return re.sub(r'H\^(.*?)\^H', r'\1', text)


class UpworkScraper:
    """Scrape Upwork jobs via GraphQL API with curl_cffi TLS impersonation."""

    def __init__(self, db):
        self.db = db
        self._token = None
        self._session = None

    def _get_session(self):
        if self._session is None:
            self._session = cffi_requests.Session(impersonate="chrome")
        return self._session

    def _fetch_token(self):
        """Fetch visitor_gql_token from Upwork homepage cookies."""
        print("  Fetching visitor token from upwork.com...")
        session = self._get_session()
        resp = session.get("https://www.upwork.com/", timeout=30)
        resp.raise_for_status()

        token = resp.cookies.get(TOKEN_COOKIE)
        if not token:
            available = list(resp.cookies.keys())
            raise RuntimeError(
                f"No {TOKEN_COOKIE} cookie found. Available: {available}"
            )

        self._token = token
        print(f"  Token acquired ({len(token)} chars)")
        return token

    def _graphql_headers(self):
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    def _search_page(self, query, offset=0):
        """Fetch one page of job search results via GraphQL."""
        variables = {
            "requestVariables": {
                "userQuery": query,
                "sort": "recency",
                "highlight": True,
                "paging": {
                    "offset": offset,
                    "count": PAGE_SIZE,
                },
            }
        }

        session = self._get_session()
        resp = session.post(
            GRAPHQL_URL,
            json={"query": JOB_SEARCH_QUERY, "variables": variables},
            headers=self._graphql_headers(),
            timeout=30,
        )

        if resp.status_code == 401:
            raise TokenExpired("Token expired, need to refresh")
        resp.raise_for_status()

        data = resp.json()

        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")

        search_result = (
            (data.get("data") or {})
            .get("search") or {}
        )
        search_result = (
            (search_result.get("universalSearchNuxt") or {})
            .get("visitorJobSearchV1") or {}
        )

        paging = search_result.get("paging", {})
        results = search_result.get("results", [])

        return paging, results

    def _parse_job(self, result, search_query):
        """Parse a single GraphQL job result into our database format."""
        title = _clean_highlight(result.get("title") or "")
        description = _clean_highlight(result.get("description") or "")

        # Skills from ontologySkills
        skills = []
        for s in (result.get("ontologySkills") or []):
            label = s.get("prefLabel", "")
            if label:
                skills.append(label)

        # Job details from nested jobTile.job
        tile = result.get("jobTile", {}) or {}
        job = tile.get("job", {}) or {}

        # Budget
        job_type_raw = job.get("jobType", "")
        hourly_min = job.get("hourlyBudgetMin")
        hourly_max = job.get("hourlyBudgetMax")
        fixed_raw = job.get("fixedPriceAmount")

        if hourly_min is not None or hourly_max is not None:
            budget_type = "Hourly"
            budget_min = float(hourly_min) if hourly_min is not None else None
            budget_max = float(hourly_max) if hourly_max is not None else None
        elif fixed_raw and fixed_raw.get("amount") is not None:
            budget_type = "Fixed"
            budget_min = float(fixed_raw["amount"])
            budget_max = None
        else:
            budget_type = job_type_raw.title() if job_type_raw else ""
            budget_min = None
            budget_max = None

        # Experience level
        tier = job.get("contractorTier", "")
        tier_map = {
            "EntryLevel": "Entry",
            "IntermediateLevel": "Intermediate",
            "ExpertLevel": "Expert",
        }
        experience_level = tier_map.get(tier, tier)

        # Job URL from ciphertext
        cipher = job.get("ciphertext", "")
        job_url = f"https://www.upwork.com/jobs/{cipher}" if cipher else ""

        # Posted date
        posted_date = job.get("publishTime", "")

        return {
            "job_url": job_url,
            "title": title,
            "description": description[:10000],
            "skills": skills,
            "budget_type": budget_type,
            "budget_min": budget_min,
            "budget_max": budget_max,
            "experience_level": experience_level,
            "client_rating": None,
            "client_total_spent": "",
            "client_hire_rate": "",
            "client_country": "",
            "num_proposals": None,
            "posted_date": posted_date,
            "category": "",
            "search_query": search_query,
        }

    def scrape(self, search_query, pages=3, delay_range=None, stop_on_dupes=False):
        """Scrape Upwork jobs for a search query. Returns count of new jobs stored.

        Args:
            delay_range: tuple (min, max) seconds between pages. Defaults to config values.
            stop_on_dupes: if True, stop a query when a page is >80% duplicates.
        """
        d_min, d_max = delay_range or (DELAY_MIN, DELAY_MAX)
        run_id = self.db.start_run("upwork", search_query)
        total = 0

        pages_done = 0
        try:
            self._fetch_token()

            for page_num in range(1, pages + 1):
                offset = (page_num - 1) * PAGE_SIZE
                print(f"\n[Page {page_num}/{pages}] offset={offset}")

                try:
                    paging, results = self._search_page(search_query, offset)
                except TokenExpired:
                    print("  Token expired, refreshing...")
                    self._fetch_token()
                    paging, results = self._search_page(search_query, offset)

                api_total = paging.get("total", 0)
                print(f"  Got {len(results)} results (API reports {api_total} total)")

                if not results:
                    print("  No results on this page, stopping.")
                    break

                page_new = 0
                for i, result in enumerate(results):
                    try:
                        job_data = self._parse_job(result, search_query)
                        title_short = job_data["title"][:55]
                        budget = ""
                        if job_data["budget_min"] is not None:
                            budget = f"${job_data['budget_min']:.0f}"
                            if job_data["budget_max"]:
                                budget += f"-${job_data['budget_max']:.0f}"

                        if self.db.insert_upwork_job(run_id, job_data):
                            total += 1
                            page_new += 1
                            skills_str = ", ".join(job_data["skills"][:3])
                            print(
                                f"  [{i+1}/{len(results)}] + {title_short}"
                                f" | {budget or 'no budget'}"
                                f" | {job_data['experience_level'] or '?'}"
                                f" | {skills_str}"
                            )
                        else:
                            print(f"  [{i+1}/{len(results)}] = {title_short} (already in DB)")
                    except Exception as e:
                        print(f"  [{i+1}/{len(results)}] ERROR: {e}")

                pages_done = page_num

                if stop_on_dupes and len(results) > 0 and page_new / len(results) < 0.2:
                    print(f"  >80% duplicates on this page ({page_new}/{len(results)} new), moving on.")
                    break

                if offset + PAGE_SIZE >= api_total:
                    print(f"  Reached end of results ({api_total} total).")
                    break

                if page_num < pages:
                    delay = random.uniform(d_min, d_max)
                    print(f"  Waiting {delay:.1f}s...")
                    time.sleep(delay)

        except Exception as e:
            print(f"\nScraper error: {e}")
            raise
        finally:
            self.db.finish_run(run_id, pages_done, total)

        return total

    def close(self):
        if self._session:
            self._session.close()
            self._session = None


class TokenExpired(Exception):
    pass
