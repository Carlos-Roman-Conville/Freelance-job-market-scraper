"""Bulk scraper — runs multiple search queries until the target gig count is reached."""

import asyncio
import sys
from pathlib import Path

# Redirected stdout falls back to cp1252 on Windows; a single emoji in a
# scraped title would otherwise kill the whole run with UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DATABASE_URL
from db import JobDatabase
from scrapers.fiverr import FiverrScraper

TARGET = 2500

QUERIES = [
    # --- Already scraped (will retry for more pages) ---
    "web scraping",
    "python automation",
    "data entry",
    "API development",
    "web development",
    "machine learning",
    "data analysis",
    "excel automation",
    "chatbot development",
    "wordpress development",
    "mobile app development",
    "database design",
    "SEO services",
    "graphic design",
    "video editing",
    "virtual assistant",
    "social media management",
    "copywriting",
    "logo design",
    "UI UX design",
    # --- New queries matching Upwork demand coverage ---
    "python development",
    "javascript development",
    "react development",
    "node.js development",
    "full stack development",
    "frontend development",
    "backend development",
    "shopify development",
    "ecommerce development",
    "game development",
    "AI development",
    "3D modeling",
    "animation",
    "QA testing",
    "cybersecurity",
    "data visualization",
    "devops",
    "AWS development",
    "cloud computing",
    "data engineering",
    "blockchain development",
    "figma design",
    "java development",
    "PHP development",
    "android development",
    "iOS development",
    "flutter development",
    "vue.js development",
    "angular development",
    "typescript development",
    "power BI",
    "technical writing",
    "computer vision",
    "prompt engineering",
    "motion graphics",
    "Salesforce development",
    "system administration",
    "C# development",
    "ruby development",
    "unity development",
    "IoT development",
    "rust development",
    "kubernetes docker",
    "penetration testing",
]


async def main():
    db = JobDatabase(DATABASE_URL)

    count = db.execute("SELECT COUNT(*) FROM fiverr_gigs").fetchone()[0]
    print(f"\n{'='*50}")
    print(f"  Bulk Scraper — Target: {TARGET} gigs")
    print(f"  Currently in DB: {count}")
    print(f"  Queries to run: {len(QUERIES)}")
    print(f"{'='*50}\n")

    if count >= TARGET:
        print(f"Already at {count} gigs, target reached!")
        db.close()
        return

    scraper = FiverrScraper(db)

    existing_queries = {
        row[0].lower()
        for row in db.execute("SELECT DISTINCT search_query FROM fiverr_gigs WHERE search_query IS NOT NULL").fetchall()
    }

    new_queries = [q for q in QUERIES if q.lower() not in existing_queries]
    old_queries = [q for q in QUERIES if q.lower() in existing_queries]
    ordered = new_queries + old_queries

    try:
        await scraper.start_browser()
        try:
            for i, query in enumerate(ordered):
                count = db.execute("SELECT COUNT(*) FROM fiverr_gigs").fetchone()[0]
                if count >= TARGET:
                    print(f"\n  Target reached! {count}/{TARGET} gigs in database.")
                    break

                remaining = TARGET - count
                pages = min(5, max(1, remaining // 40))

                status = "NEW" if query in new_queries else "RETRY"
                print(f"\n[{i+1}/{len(ordered)}] Query: \"{query}\" ({status}) — {count}/{TARGET} gigs so far — scraping {pages} page(s)")

                try:
                    scraped = await scraper.scrape_query(query, pages)
                    print(f"  -> Got {scraped} gigs from \"{query}\"")
                except Exception as e:
                    print(f"  -> ERROR on \"{query}\": {e}")
                    try:
                        await scraper.stop_browser()
                        await scraper.start_browser()
                        print("  Browser restarted")
                    except Exception as restart_err:
                        print(f"  Browser restart failed: {restart_err}")
                        break
                    continue
        finally:
            await scraper.stop_browser()
    finally:
        try:
            count = db.execute("SELECT COUNT(*) FROM fiverr_gigs").fetchone()[0]
            print(f"\n{'='*50}")
            print(f"  Done! Total gigs in database: {count}/{TARGET}")
            print(f"{'='*50}")
        except Exception:
            print("\n  Could not fetch final count (connection lost)")
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
