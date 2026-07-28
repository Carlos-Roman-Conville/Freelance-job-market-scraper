"""Bulk Upwork scraper — runs multiple search queries to maximize job coverage.

API limit: max offset 5000, so each query yields up to ~5050 jobs.
Uses 0.5-1.5s delays (GraphQL API, not browser) and stops early on
duplicate-heavy pages to avoid wasting time on overlapping queries.
"""

import sys
import time
from pathlib import Path

# Redirected stdout falls back to cp1252 on Windows; a single emoji in a
# scraped title would otherwise kill the whole run with UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DATABASE_URL
from db import JobDatabase
from scrapers.upwork import UpworkScraper

TARGET = 90000
MAX_PAGES_PER_QUERY = 101  # 101 pages * 50 = 5050, the API offset cap
DELAY_RANGE = (0.5, 1.5)

QUERIES = [
    # --- Tech / Development ---
    "web development",
    "web scraping",
    "python developer",
    "javascript developer",
    "react developer",
    "node.js developer",
    "angular developer",
    "vue.js developer",
    "full stack developer",
    "frontend developer",
    "backend developer",
    "API development",
    "mobile app development",
    "android developer",
    "iOS developer",
    "flutter developer",
    "swift developer",
    "kotlin developer",
    "wordpress development",
    "shopify development",
    "php developer",
    "ruby developer",
    "java developer",
    "C++ developer",
    "C# developer",
    "rust developer",
    "go developer",
    "TypeScript developer",
    "game development",
    "unity developer",
    "database design",
    "SQL developer",
    # --- AI / Data ---
    "machine learning",
    "AI developer",
    "data analysis",
    "data entry",
    "data engineer",
    "data visualization",
    "power BI",
    "tableau developer",
    "NLP natural language processing",
    "computer vision",
    "chatbot development",
    "LLM prompt engineering",
    # --- DevOps / Infrastructure ---
    "devops engineer",
    "cloud computing",
    "AWS developer",
    "kubernetes docker",
    "terraform infrastructure",
    "system administration",
    "linux administrator",
    "cybersecurity",
    "penetration testing",
    # --- Design ---
    "graphic design",
    "UI UX design",
    "logo design",
    "figma design",
    "product design",
    "brand identity design",
    "illustration",
    "3D modeling",
    "animation",
    "photo editing retouching",
    # --- Writing / Content ---
    "copywriting",
    "content writing",
    "technical writing",
    "blog writing",
    "translation",
    "transcription",
    "proofreading editing",
    "grant writing",
    "resume writing",
    "ghostwriting",
    # --- Video / Audio ---
    "video editing",
    "video production",
    "motion graphics",
    "voiceover",
    "audio editing",
    "podcast editing",
    # --- Marketing ---
    "SEO services",
    "social media management",
    "email marketing",
    "Google Ads PPC",
    "Facebook Ads",
    "digital marketing",
    "lead generation",
    "market research",
    "affiliate marketing",
    # --- Business / Admin ---
    "virtual assistant",
    "project management",
    "business consulting",
    "bookkeeping accounting",
    "financial analysis",
    "customer service",
    "executive assistant",
    "data scraping automation",
    "excel automation",
    "CRM Salesforce",
    # --- Specialized ---
    "ecommerce development",
    "ERP SAP",
    "QA testing",
    "manual testing automation",
    "blockchain developer",
    "smart contract solidity",
    "IoT embedded systems",
    "CAD drafting",
    "supply chain logistics",
    "healthcare medical writing",
    "legal research writing",
    "real estate virtual tours",
    "Amazon FBA listing",
]

assert len(QUERIES) == len(set(QUERIES)), "Duplicate queries found!"


def main():
    db = JobDatabase(DATABASE_URL)

    count = db.execute("SELECT COUNT(*) FROM upwork_jobs").fetchone()[0]
    print(f"\n{'='*60}")
    print(f"  Upwork MEGA Scraper — Target: {TARGET:,} jobs")
    print(f"  Currently in DB: {count:,}")
    print(f"  Queries: {len(QUERIES)}")
    print(f"  Max pages per query: {MAX_PAGES_PER_QUERY} ({MAX_PAGES_PER_QUERY * 50:,} jobs)")
    print(f"  Delay between pages: {DELAY_RANGE[0]}-{DELAY_RANGE[1]}s")
    print(f"{'='*60}\n")

    if count >= TARGET:
        print(f"Already at {count:,} jobs, target reached!")
        db.close()
        return

    scraper = UpworkScraper(db)

    existing_queries = {
        row[0].lower()
        for row in db.execute("SELECT DISTINCT search_query FROM upwork_jobs WHERE search_query IS NOT NULL").fetchall()
    }

    new_queries = [q for q in QUERIES if q.lower() not in existing_queries]
    old_queries = [q for q in QUERIES if q.lower() in existing_queries]
    ordered = new_queries + old_queries

    start_time = time.time()
    start_count = db.execute("SELECT COUNT(*) FROM upwork_jobs").fetchone()[0]
    queries_done = 0

    try:
        for i, query in enumerate(ordered):
            count = db.execute("SELECT COUNT(*) FROM upwork_jobs").fetchone()[0]
            if count >= TARGET:
                print(f"\n  Target reached! {count:,}/{TARGET:,} jobs in database.")
                break

            remaining = TARGET - count
            pages = min(MAX_PAGES_PER_QUERY, max(1, remaining // 40))

            status = "NEW" if query in new_queries else "MORE"
            elapsed = time.time() - start_time
            session_added = count - start_count
            rate = session_added / max(elapsed, 1) * 3600
            print(f"\n{'—'*60}")
            print(
                f"[{i+1}/{len(ordered)}] \"{query}\" ({status})"
                f" — {count:,}/{TARGET:,} jobs"
                f" — {pages} pages"
                f" — {elapsed/60:.0f}min elapsed"
                f" — ~{rate:.0f} jobs/hr"
            )

            try:
                scraped = scraper.scrape(
                    query, pages,
                    delay_range=DELAY_RANGE,
                    stop_on_dupes=True,
                )
                queries_done += 1
                print(f"  -> Got {scraped} new jobs from \"{query}\"")
            except Exception as e:
                print(f"  -> ERROR on \"{query}\": {e}")
                if "429" in str(e) or "Too Many" in str(e):
                    print("  Rate limited! Waiting 60s...")
                    time.sleep(60)
                continue
    finally:
        try:
            scraper.close()
        except Exception:
            pass
        try:
            count = db.execute("SELECT COUNT(*) FROM upwork_jobs").fetchone()[0]
            elapsed = time.time() - start_time
            session_added = count - start_count
            print(f"\n{'='*60}")
            print(f"  Done! Total Upwork jobs: {count:,}/{TARGET:,}")
            print(f"  New this session: {session_added:,}")
            print(f"  Queries completed: {queries_done}/{len(ordered)}")
            print(f"  Time: {elapsed/60:.1f} minutes")
            print(f"  Rate: {session_added / max(elapsed, 1) * 3600:.0f} jobs/hour")
            print(f"{'='*60}")
        except Exception:
            print("\n  Could not fetch final count (connection lost)")
        db.close()


if __name__ == "__main__":
    main()
