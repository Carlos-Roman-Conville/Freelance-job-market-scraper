import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class JobDatabase:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                platform        TEXT NOT NULL,
                search_query    TEXT NOT NULL,
                pages_scraped   INTEGER,
                jobs_found      INTEGER,
                started_at      TEXT NOT NULL,
                finished_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS upwork_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scrape_run_id   INTEGER REFERENCES scrape_runs(id),
                job_url         TEXT UNIQUE,
                title           TEXT NOT NULL,
                description     TEXT,
                skills          TEXT,
                budget_type     TEXT,
                budget_min      REAL,
                budget_max      REAL,
                experience_level TEXT,
                client_rating   REAL,
                client_total_spent TEXT,
                client_hire_rate   TEXT,
                client_country  TEXT,
                num_proposals   INTEGER,
                posted_date     TEXT,
                category        TEXT,
                scraped_at      TEXT NOT NULL,
                search_query    TEXT
            );

            CREATE TABLE IF NOT EXISTS fiverr_gigs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scrape_run_id   INTEGER REFERENCES scrape_runs(id),
                gig_url         TEXT UNIQUE,
                title           TEXT NOT NULL,
                seller_name     TEXT,
                seller_level    TEXT,
                price_min       REAL,
                price_max       REAL,
                num_reviews     INTEGER,
                rating          REAL,
                category        TEXT,
                tags            TEXT,
                scraped_at      TEXT NOT NULL,
                search_query    TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_upwork_scraped_at ON upwork_jobs(scraped_at);
            CREATE INDEX IF NOT EXISTS idx_upwork_search ON upwork_jobs(search_query);
            CREATE INDEX IF NOT EXISTS idx_upwork_category ON upwork_jobs(category);
            CREATE INDEX IF NOT EXISTS idx_upwork_experience ON upwork_jobs(experience_level);
            CREATE INDEX IF NOT EXISTS idx_fiverr_scraped_at ON fiverr_gigs(scraped_at);
            CREATE INDEX IF NOT EXISTS idx_fiverr_search ON fiverr_gigs(search_query);
            CREATE INDEX IF NOT EXISTS idx_fiverr_category ON fiverr_gigs(category);
        """)

        # FTS5 tables for text search (RAG-ready)
        try:
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS upwork_jobs_fts USING fts5(
                    title, description, skills, category,
                    content='upwork_jobs', content_rowid='id'
                )
            """)
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS fiverr_gigs_fts USING fts5(
                    title, category, tags,
                    content='fiverr_gigs', content_rowid='id'
                )
            """)
        except sqlite3.OperationalError:
            pass

        # Triggers to keep FTS in sync
        for trigger_sql in [
            """CREATE TRIGGER IF NOT EXISTS upwork_ai AFTER INSERT ON upwork_jobs BEGIN
                INSERT INTO upwork_jobs_fts(rowid, title, description, skills, category)
                VALUES (new.id, new.title, new.description, new.skills, new.category);
            END""",
            """CREATE TRIGGER IF NOT EXISTS fiverr_ai AFTER INSERT ON fiverr_gigs BEGIN
                INSERT INTO fiverr_gigs_fts(rowid, title, category, tags)
                VALUES (new.id, new.title, new.category, new.tags);
            END""",
        ]:
            try:
                self.conn.execute(trigger_sql)
            except sqlite3.OperationalError:
                pass

        self.conn.commit()

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def start_run(self, platform, query):
        cur = self.conn.execute(
            "INSERT INTO scrape_runs (platform, search_query, started_at) VALUES (?, ?, ?)",
            (platform, query, self._now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id, pages_scraped, jobs_found):
        self.conn.execute(
            "UPDATE scrape_runs SET finished_at=?, pages_scraped=?, jobs_found=? WHERE id=?",
            (self._now(), pages_scraped, jobs_found, run_id),
        )
        self.conn.commit()

    def insert_upwork_job(self, run_id, data):
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO upwork_jobs
                   (scrape_run_id, job_url, title, description, skills,
                    budget_type, budget_min, budget_max, experience_level,
                    client_rating, client_total_spent, client_hire_rate,
                    client_country, num_proposals, posted_date, category,
                    scraped_at, search_query)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    data.get("job_url", ""),
                    data.get("title", ""),
                    data.get("description", ""),
                    json.dumps(data.get("skills", [])),
                    data.get("budget_type", ""),
                    data.get("budget_min"),
                    data.get("budget_max"),
                    data.get("experience_level", ""),
                    data.get("client_rating"),
                    data.get("client_total_spent", ""),
                    data.get("client_hire_rate", ""),
                    data.get("client_country", ""),
                    data.get("num_proposals"),
                    data.get("posted_date", ""),
                    data.get("category", ""),
                    self._now(),
                    data.get("search_query", ""),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def insert_fiverr_gig(self, run_id, data):
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO fiverr_gigs
                   (scrape_run_id, gig_url, title, seller_name, seller_level,
                    price_min, price_max, num_reviews, rating,
                    category, tags, scraped_at, search_query)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    data.get("gig_url", ""),
                    data.get("title", ""),
                    data.get("seller_name", ""),
                    data.get("seller_level", ""),
                    data.get("price_min"),
                    data.get("price_max"),
                    data.get("num_reviews"),
                    data.get("rating"),
                    data.get("category", ""),
                    json.dumps(data.get("tags", [])),
                    self._now(),
                    data.get("search_query", ""),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_upwork_jobs(self, query=None, limit=500):
        if query:
            rows = self.conn.execute(
                "SELECT * FROM upwork_jobs WHERE search_query=? ORDER BY scraped_at DESC LIMIT ?",
                (query, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM upwork_jobs ORDER BY scraped_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_fiverr_gigs(self, query=None, limit=500):
        if query:
            rows = self.conn.execute(
                "SELECT * FROM fiverr_gigs WHERE search_query=? ORDER BY scraped_at DESC LIMIT ?",
                (query, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM fiverr_gigs ORDER BY scraped_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def search_text(self, platform, query):
        if platform == "upwork":
            rows = self.conn.execute(
                """SELECT u.* FROM upwork_jobs u
                   JOIN upwork_jobs_fts f ON u.id = f.rowid
                   WHERE upwork_jobs_fts MATCH ?
                   ORDER BY rank LIMIT 50""",
                (query,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT g.* FROM fiverr_gigs g
                   JOIN fiverr_gigs_fts f ON g.id = f.rowid
                   WHERE fiverr_gigs_fts MATCH ?
                   ORDER BY rank LIMIT 50""",
                (query,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self):
        stats = {}
        stats["upwork_total"] = self.conn.execute("SELECT COUNT(*) FROM upwork_jobs").fetchone()[0]
        stats["fiverr_total"] = self.conn.execute("SELECT COUNT(*) FROM fiverr_gigs").fetchone()[0]
        stats["total_runs"] = self.conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0]

        row = self.conn.execute(
            "SELECT MIN(scraped_at), MAX(scraped_at) FROM upwork_jobs"
        ).fetchone()
        stats["upwork_first"] = row[0] or "N/A"
        stats["upwork_last"] = row[1] or "N/A"

        row = self.conn.execute(
            "SELECT MIN(scraped_at), MAX(scraped_at) FROM fiverr_gigs"
        ).fetchone()
        stats["fiverr_first"] = row[0] or "N/A"
        stats["fiverr_last"] = row[1] or "N/A"

        stats["upwork_queries"] = [
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT search_query FROM upwork_jobs ORDER BY search_query"
            ).fetchall()
        ]
        stats["fiverr_queries"] = [
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT search_query FROM fiverr_gigs ORDER BY search_query"
            ).fetchall()
        ]
        return stats

    def close(self):
        self.conn.close()
