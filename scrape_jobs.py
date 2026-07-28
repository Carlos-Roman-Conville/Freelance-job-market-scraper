"""
Job Market Scraper — Scrapes Upwork (GraphQL API) and Fiverr (nodriver) job listings.
Stores data in PostgreSQL for later RAG analysis with Claude API.

Usage:
    python scrape_jobs.py upwork --search "web scraping" --pages 3
    python scrape_jobs.py fiverr --search "data entry" --pages 2
    python scrape_jobs.py export upwork --query "web scraping"
    python scrape_jobs.py export fiverr
    python scrape_jobs.py stats

Note: Close Chrome before using --chrome flag for Fiverr (can't share profile with running Chrome).
"""
import argparse
import asyncio
import sys

# Two problems when stdout is redirected to a file on Windows: it falls back to
# cp1252 (one emoji in a scraped title kills the run), and it block-buffers, so a
# multi-hour scrape shows no progress until it exits.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from config import DATABASE_URL, DEFAULT_PAGES
from db import JobDatabase
from export import export_upwork_excel, export_fiverr_excel


def cmd_upwork(args, db):
    from scrapers.upwork import UpworkScraper
    scraper = UpworkScraper(db)
    try:
        count = scraper.scrape(args.search, args.pages)
    finally:
        scraper.close()
    print(f"\n{'=' * 50}")
    print(f"  Scraped {count} Upwork jobs for '{args.search}'")
    print(f"{'=' * 50}")

    if args.export:
        jobs = db.get_upwork_jobs(query=args.search, limit=None)
        export_upwork_excel(jobs)


def cmd_fiverr(args, db):
    from scrapers.fiverr import FiverrScraper
    scraper = FiverrScraper(db, use_chrome=args.chrome)
    count = asyncio.run(scraper.scrape(args.search, args.pages))
    print(f"\n{'=' * 50}")
    print(f"  Scraped {count} Fiverr gigs for '{args.search}'")
    print(f"{'=' * 50}")

    if args.export:
        gigs = db.get_fiverr_gigs(query=args.search, limit=None)
        export_fiverr_excel(gigs)


def cmd_export(args, db):
    if args.platform == "upwork":
        jobs = db.get_upwork_jobs(query=args.query, limit=args.limit)
        export_upwork_excel(jobs)
    else:
        gigs = db.get_fiverr_gigs(query=args.query, limit=args.limit)
        export_fiverr_excel(gigs)


def cmd_stats(args, db):
    stats = db.get_stats()
    print(f"\n{'=' * 50}")
    print(f"  JOB MARKET SCRAPER — DATABASE STATS")
    print(f"{'=' * 50}")
    print(f"\n  Total scrape runs:  {stats['total_runs']}")
    print(f"\n  UPWORK")
    print(f"    Jobs stored:      {stats['upwork_total']}")
    print(f"    First scraped:    {stats['upwork_first']}")
    print(f"    Last scraped:     {stats['upwork_last']}")
    if stats['upwork_queries']:
        print(f"    Search queries:   {', '.join(stats['upwork_queries'])}")
    print(f"\n  FIVERR")
    print(f"    Gigs stored:      {stats['fiverr_total']}")
    print(f"    First scraped:    {stats['fiverr_first']}")
    print(f"    Last scraped:     {stats['fiverr_last']}")
    if stats['fiverr_queries']:
        print(f"    Search queries:   {', '.join(stats['fiverr_queries'])}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Job Market Scraper — Upwork & Fiverr",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command")

    # Upwork scraper (uses GraphQL API, no browser needed)
    up = subparsers.add_parser("upwork", help="Scrape Upwork job listings")
    up.add_argument("--search", required=True, help="Search query")
    up.add_argument("--pages", type=int, default=DEFAULT_PAGES, help=f"Pages to scrape (default: {DEFAULT_PAGES})")
    up.add_argument("--export", action="store_true", help="Export results to Excel after scraping")

    # Fiverr scraper
    fv = subparsers.add_parser("fiverr", help="Scrape Fiverr gig listings")
    fv.add_argument("--search", required=True, help="Search query")
    fv.add_argument("--pages", type=int, default=DEFAULT_PAGES, help=f"Pages to scrape (default: {DEFAULT_PAGES})")
    fv.add_argument("--export", action="store_true", help="Export results to Excel after scraping")
    fv.add_argument("--chrome", action="store_true", help="Use your real Chrome profile (close Chrome first!)")

    # Export
    ex = subparsers.add_parser("export", help="Export stored data to Excel")
    ex.add_argument("platform", choices=["upwork", "fiverr"], help="Platform to export")
    ex.add_argument("--query", default=None, help="Filter by search query")
    ex.add_argument("--limit", type=int, default=None,
                    help="Max rows to export (default: all)")

    # Stats
    subparsers.add_parser("stats", help="Show database statistics")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    db = JobDatabase(DATABASE_URL)

    try:
        if args.command == "upwork":
            cmd_upwork(args, db)
        elif args.command == "fiverr":
            cmd_fiverr(args, db)
        elif args.command == "export":
            cmd_export(args, db)
        elif args.command == "stats":
            cmd_stats(args, db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
