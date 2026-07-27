"""Re-visit gigs that have empty descriptions and update them with the fixed parser."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

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
                    params.append(gig_id)
                    db.execute(
                        "UPDATE fiverr_gigs SET " + ", ".join(updates) + " WHERE id = %s",
                        params,
                    )
                    db.conn.commit()
                    new_tags = data.get("tags", [])
                    if new_tags:
                        try:
                            new_canonical = [_normalize_tag(t) for t in new_tags]
                            new_canonical = [c for c in new_canonical if c is not None]
                            existing_count = db.execute(
                                "SELECT COUNT(*) FROM gig_skills WHERE gig_id = %s", (gig_id,)
                            ).fetchone()[0]
                            if len(new_canonical) >= existing_count:
                                db.execute("DELETE FROM gig_skills WHERE gig_id = %s", (gig_id,))
                            for canonical in new_canonical:
                                db._link_skill(gig_id, canonical)
                            db.conn.commit()
                        except Exception:
                            db.conn.rollback()
                    desc_len = len(data.get("description", ""))
                    tag_count = len(new_tags)
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
