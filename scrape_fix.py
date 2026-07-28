"""Re-visit gigs that have empty descriptions and update them with the fixed parser."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Redirected stdout falls back to cp1252 on Windows; a single emoji in a
# scraped title would otherwise kill the whole run with UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import DATABASE_URL
from db import JobDatabase, _normalize_tag
from scrapers.fiverr import FiverrScraper
from scrapers.base import BaseScraper


async def main():
    db = JobDatabase(DATABASE_URL)

    # Find gigs missing descriptions
    missing = db.execute("""
        SELECT id, gig_url, title, seller_name, seller_level, price_min, num_reviews, rating
        FROM fiverr_gigs
        WHERE (description IS NULL OR description = '')
        AND gig_url != ''
    """).fetchall()

    total = len(missing)
    print(f"\n{'='*50}")
    print(f"  Re-scraping {total} gigs with missing descriptions")
    print(f"{'='*50}\n")

    if total == 0:
        print("Nothing to fix!")
        return

    scraper = FiverrScraper(db)
    await scraper.start_browser()

    fixed = 0
    errors = 0

    try:
        for i, row in enumerate(missing):
            gig_id = row["id"]
            url = row["gig_url"]
            title = row["title"][:50]
            print(f"  [{i+1}/{total}] {title}")

            await scraper.random_delay()

            try:
                content = await scraper.navigate(url, wait=10)

                if scraper._is_blocked(content):
                    print(f"    BLOCKED")
                    errors += 1
                    continue

                if scraper._is_404(content.lower()):
                    print(f"    404 — gig removed, skipping")
                    continue

                card = {
                    "gig_url": row["gig_url"],
                    "title": row["title"],
                    "seller_name": row["seller_name"],
                    "seller_level": row["seller_level"] or "",
                    "price_min": row["price_min"],
                    "num_reviews": row["num_reviews"],
                    "rating": row["rating"],
                }
                data = scraper._parse_detail_page(content, card)

                # Update the row with new data
                updates = []
                params = []
                ALLOWED_COLS = {"description", "seller_languages", "seller_orders_completed",
                               "hourly_rate", "delivery_days", "tags", "seller_country",
                               "category", "subcategory", "price_max"}
                for col in ALLOWED_COLS:
                    val = data.get(col)
                    if col == "tags":
                        if not val:
                            continue
                        val = json.dumps(val)
                    elif val is None or val == "":
                        continue
                    updates.append(f"{col} = %s")
                    params.append(val)

                if updates:
                    for u in updates:
                        col_name = u.split(" = ")[0]
                        assert col_name in ALLOWED_COLS, f"unexpected column: {col_name}"

                    new_tags = data.get("tags", [])
                    new_canonical = [c for c in (_normalize_tag(t) for t in new_tags)
                                     if c is not None]
                    existing_count = db.execute(
                        "SELECT COUNT(*) FROM gig_skills WHERE gig_id = %s", (gig_id,)
                    ).fetchone()[0]

                    # A re-scrape yielding fewer skills is likely a partial parse —
                    # skip the tags column too so it can't diverge from gig_skills.
                    replace_skills = bool(new_canonical) and len(new_canonical) >= existing_count
                    if not replace_skills:
                        keep = [(u, p) for u, p in zip(updates, params)
                                if not u.startswith("tags ")]
                        updates = [u for u, _ in keep]
                        params = [p for _, p in keep]

                    if updates:
                        params.append(gig_id)
                        db.execute(
                            "UPDATE fiverr_gigs SET " + ", ".join(updates) + " WHERE id = %s",
                            params,
                        )
                        if replace_skills:
                            db.execute("DELETE FROM gig_skills WHERE gig_id = %s", (gig_id,))
                            for canonical in new_canonical:
                                db._link_skill(gig_id, canonical)
                        db.conn.commit()

                    desc_len = len(data.get("description", ""))
                    tag_count = len(new_canonical) if replace_skills else 0
                    print(f"    FIXED: {desc_len} chars, {tag_count} tags")
                    fixed += 1
                else:
                    print(f"    No data extracted")

            except Exception as e:
                print(f"    ERROR: {e}")
                try:
                    db.conn.rollback()
                except Exception:
                    pass
                errors += 1
    finally:
        try:
            await scraper.stop_browser()
        except Exception:
            pass
        db.close()

    print(f"\n{'='*50}")
    print(f"  Done! Fixed {fixed}/{total}, Errors: {errors}")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
