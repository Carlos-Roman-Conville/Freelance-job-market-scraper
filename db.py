import html
import json
import re

import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

# Structural noise from Fiverr pages — not real skills
_TAG_NOISE = {
    # Fiverr UI / section headers
    "expertise:", "programming language:", "platform:", "frameworks:",
    "tools:", "classification", "project cost", "project duration",
    "offers video consultations", "highly responsive",
    "known for exceptionally quick replies", "read more", "see all",
    "contact me", "vetted for", "technology:", "information type:",
    "technique:", "type:", "level 1", "level 2", "top rated",
    "pro verified", "buyers keep", "returning", "full screen",
    "see more", "helpful", "yes", "no", "show more reviews",
    "sort by", "most relevant", "related tags", "automated",
    "image processing", "images", "+2 more", "+3 more",
    "categories", "for clients", "for freelancers", "business solutions",
    "company", "repeat client", "seller's response", "select",
    "contact information", "listings",
    # Not skills — too generic or not actionable
    "text", "other", "none", "n/a", "basic", "standard", "premium",
    "price", "price comparison", "comparison", "development",
    "website", "business", "construction", "lifestyle",
    "project-based", "weekly assistance",
    # Languages (spoken, not programming)
    "english", "urdu", "hindi", "arabic", "spanish", "french",
    "german", "portuguese", "italian", "chinese", "japanese",
    "korean", "russian", "turkish", "bengali", "gujarati",
    # Fiverr industry/category labels
    "animals & pets", "animals &amp; pets",
    "art & design", "art &amp; design",
    "beauty & cosmetics", "beauty &amp; cosmetics",
    "agriculture", "marketing & advertising", "marketing &amp; advertising",
    "financial services & business", "financial services &amp; business",
    "business services & consulting", "business services &amp; consulting",
}

# Countries that leak into tags from page text
_COUNTRIES = {
    "pakistan", "india", "united states", "bangladesh", "philippines",
    "nigeria", "kenya", "egypt", "sri lanka", "vietnam", "indonesia",
    "brazil", "ukraine", "turkey", "romania", "china",
    "united kingdom", "canada", "germany", "france", "australia",
    "israel", "morocco", "tunisia", "spain", "netherlands", "italy",
    "sweden", "denmark", "norway", "south africa", "colombia",
    "mexico", "argentina", "poland", "greece", "portugal",
    "ireland", "belgium", "switzerland", "austria", "new zealand",
    "singapore", "malaysia", "thailand", "japan", "south korea",
    "haiti", "zambia", "qatar",
}


# Canonical name mapping — merges duplicates and normalizes casing
_CANONICAL = {
    # Programming language/framework variants
    "nodejs": "Node.js", "node.js": "Node.js",
    "reactjs": "React", "react": "React", "react native": "React Native",
    "javascript": "JavaScript", "java script": "JavaScript",
    "nextjs": "Next.js",
    "panda": "Pandas", "pandas": "Pandas",
    "django": "Django", "python django": "Django",
    "flask": "Flask", "python flask": "Flask",
    "flutter": "Flutter",
    "laravel": "Laravel", "php laravel": "Laravel",
    "php": "PHP",
    "html": "HTML", "html5": "HTML",
    "css": "CSS", "css3": "CSS",
    "supabase": "Supabase",
    "mongodb": "MongoDB",
    "postgresql": "PostgreSQL",
    "n8n": "n8n",
    "rest api": "REST API",
    "flutterflow": "FlutterFlow",
    "elementor": "Elementor",
    "sentimental analysis": "Sentiment Analysis",
    "ab test": "A/B Testing",
    "telegram bot": "Telegram Bot",

    # Tool / platform variants
    "power bi": "Power BI", "microsoft power bi": "Power BI",
    "excel": "Excel", "microsoft excel": "Excel",
    "vba": "VBA", "excel vba": "VBA",
    "powerpoint": "PowerPoint", "microsoft powerpoint": "PowerPoint",
    "google sheets": "Google Sheets", "google spreadsheet": "Google Sheets",
    "ms access": "MS Access", "microsoft access": "MS Access",
    "mysql": "MySQL", "mysql database": "MySQL",
    "ms sql": "SQL Server", "sql server": "SQL Server",
    "spss": "SPSS", "ibm spss": "SPSS",

    # Platform consolidation
    "wordpress": "WordPress", "wordpress website": "WordPress",
    "wordpress elementor": "Elementor", "elementor pro": "Elementor",
    "wix website": "Wix", "wix website design": "Wix", "wix website redesign": "Wix",

    # Skill name dedup
    "sql": "SQL", "sql development": "SQL", "sql database": "SQL", "sql queries": "SQL",
    "php development": "PHP",
    "canva design": "Canva",

    # Data entry consolidation
    "data entry": "Data Entry",
    "data entry ms word": "Data Entry", "data entry ms excel": "Data Entry",
    "data entry ms office": "Data Entry",

    # Excel sub-skills → keep as distinct but normalized
    "excel macros": "Excel Macros", "macros": "Excel Macros",
    "excel automation": "Excel Automation",
    "excel dashboard": "Excel Dashboard",
    "ms access vba": "MS Access VBA",
}


def _is_noise_tag(tag):
    low = tag.lower().strip()
    if low in _TAG_NOISE:
        return True
    if low in _COUNTRIES:
        return True
    if low.endswith(":") and len(low) < 30:
        return True
    if re.match(r'^\$[\d,.]+', low) or re.match(r'^\d+[-–]\d+', low):
        return True
    if re.match(r'^\+\d+', low):
        return True
    if len(low) < 2 or len(low) > 60:
        return True
    if re.match(r'^[\d\s]+$', low):
        return True
    if re.match(r'^[\W_]+$', low):
        return True
    if re.match(r'^\d+\+?\s*(days?|weeks?|months?|hours?|minutes?|yrs?)$', low):
        return True
    if re.match(r'^from:', low):
        return True
    if re.match(r'^\(?\$\d', low) or re.match(r'^up to \$', low):
        return True
    if '&amp;' in low or '&#' in low:
        return True
    if low in ("more from", "continue", "services", "projects",
               "repeat buyer", "go to"):
        return True
    if low in ("real estate", "digital marketing", "education",
               "e-commerce", "social media"):
        return True
    if low in ("date", "numeric", "string", "free text", "duration",
               "currency", "custom", "design", "programming", "editing",
               "fixing", "performance", "trends", "prediction",
               "functions", "forms", "formatting", "churn",
               "integration", "formulas"):
        return True
    if low in ("copy paste work", "insert data", "retyping",
               "convert data", "data input", "other spreadsheets",
               "word document", "research peptide", "peptide website",
               "peptide", "repeat buyer", "categories",
               "word", "spreadsheet", "charts", "math",
               "excel work", "excel formatting", "excel spreadsheet",
               "excel reporting", "word formatting", "data formatting",
               "pdf conversion", "pdf editing", "data collecting",
               "typing", "articles writing", "email management"):
        return True
    if low in ("api", "backend", "mobile app", "chatbot", "automation",
               "automations", "ai app", "ai software", "ai mobile app",
               "app creation", "app builder", "website",
               "business website", "algorithms"):
        return True
    if re.match(r'^(make|build|create|redesign|design|custom|full stack)\s+(website|wordpress|wix)', low):
        return True
    if low in ("make website", "building mobile app",
               "landing page", "wordpress blog",
               "ecommerce website", "ai web app",
               "ai web application", "website redesign"):
        return True
    if re.match(r'.*(developer|designer)s?$', low) and len(low.split()) <= 3:
        return True
    if low in ("virtual assistance", "customer service"):
        return True
    # Deliverables and generic categories, not learnable skills
    if low in ("pitch deck", "custom chatbot", "dashboard",
               "ai website", "ai chatbot", "ai agent", "ai saas",
               "ai integration", "ai app development", "ai development",
               "ai web development",
               "backend development", "app development",
               "ios app development", "android app",
               "web app development", "website builder",
               "full stack", "api development", "saas development",
               "desktop applications development",
               "software development", "flutter app",
               "business automation", "dashboard design",
               "dashboards development", "website design",
               "website development", "web development",
               "web design"):
        return True
    # Task descriptions / activities, not skills
    if low in ("data collection", "web research", "email sourcing",
               "pdf editor", "pdf to excel", "statistics tutoring",
               "report writing", "data flow", "data preprocessing",
               "data reporting", "market research",
               "b2b lead generation", "lead generation",
               "linkedin lead generation", "financial statements",
               "business statistics", "business insights",
               "descriptive analysis", "data entry ms word",
               "html css", "data acquisition", "research analysis",
               "data management"):
        return True
    # Generic database terms (specific tools like MySQL, PostgreSQL are kept)
    if low in ("html css", "html css javascript", "html css js",
               "ai saas app", "third party api",
               "full stack web development", "front-end web development",
               "web application development",
               "social media marketing", "social media management",
               "email list building", "targeted email list",
               "big data entry", "crm data entry", "wordpress data entry",
               "creative content writing", "marketing strategy consulting",
               "real estate skip tracing", "real estate virtual assistance"):
        return True
    if re.match(r'^[a-z]+ \d{4}', low):
        return True
    if low in ("database programming", "database management",
               "database design", "database optimization",
               "centralized database", "distributed database",
               "hierarchical database", "non-relational database",
               "relational database", "key-value database",
               "graph database"):
        return True
    return False


def _normalize_tag(tag):
    """Return canonical display name, or None if noise."""
    if not isinstance(tag, str):
        return None
    tag = re.sub(r'\s+', ' ', html.unescape(tag)).strip()
    if _is_noise_tag(tag):
        return None
    low = tag.lower()
    if low in _CANONICAL:
        return _CANONICAL[low]
    return tag


# Bump when _create_tables() changes so existing deployments re-run the DDL.
SCHEMA_VERSION = 1


class JobDatabase:
    def __init__(self, dsn):
        self.conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.DictCursor)
        try:
            if self._needs_schema_setup():
                self._create_tables()
        except Exception:
            self.conn.close()
            raise

    def _needs_schema_setup(self):
        """True if the schema is missing or stale.

        _create_tables() takes ACCESS EXCLUSIVE locks (DROP/CREATE TRIGGER), so
        running it on every construction stalls any scraper already inserting.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        id      INTEGER PRIMARY KEY CHECK (id = 1),
                        version INTEGER NOT NULL
                    )
                """)
                cur.execute("SELECT version FROM schema_meta WHERE id = 1")
                row = cur.fetchone()
            self.conn.commit()
            return row is None or row[0] != SCHEMA_VERSION
        except Exception:
            self.conn.rollback()
            return True

    def execute(self, query, params=None):
        """Execute a query and return the cursor (for external callers)."""
        cur = self.conn.cursor()
        cur.execute(query, params)
        return cur

    def _create_tables(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scrape_runs (
                    id              SERIAL PRIMARY KEY,
                    platform        TEXT NOT NULL,
                    search_query    TEXT NOT NULL,
                    pages_scraped   INTEGER,
                    jobs_found      INTEGER,
                    started_at      TEXT NOT NULL,
                    finished_at     TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS upwork_jobs (
                    id              SERIAL PRIMARY KEY,
                    scrape_run_id   INTEGER REFERENCES scrape_runs(id),
                    job_url         TEXT UNIQUE,
                    title           TEXT NOT NULL,
                    description     TEXT,
                    skills          TEXT,
                    budget_type     TEXT,
                    budget_min      DOUBLE PRECISION,
                    budget_max      DOUBLE PRECISION,
                    experience_level TEXT,
                    client_rating   DOUBLE PRECISION,
                    client_total_spent TEXT,
                    client_hire_rate   TEXT,
                    client_country  TEXT,
                    num_proposals   INTEGER,
                    posted_date     TEXT,
                    category        TEXT,
                    scraped_at      TEXT NOT NULL,
                    search_query    TEXT,
                    search_vector   TSVECTOR
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fiverr_gigs (
                    id              SERIAL PRIMARY KEY,
                    scrape_run_id   INTEGER REFERENCES scrape_runs(id),
                    gig_url         TEXT UNIQUE,
                    title           TEXT NOT NULL,
                    description     TEXT,
                    seller_name     TEXT,
                    seller_level    TEXT,
                    seller_country  TEXT,
                    seller_languages TEXT,
                    seller_orders_completed INTEGER,
                    price_min       DOUBLE PRECISION,
                    price_max       DOUBLE PRECISION,
                    hourly_rate     DOUBLE PRECISION,
                    delivery_days   INTEGER,
                    num_reviews     INTEGER,
                    rating          DOUBLE PRECISION,
                    category        TEXT,
                    subcategory     TEXT,
                    tags            TEXT,
                    scraped_at      TEXT NOT NULL,
                    search_query    TEXT,
                    search_vector   TSVECTOR
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS skills (
                    id              SERIAL PRIMARY KEY,
                    name            TEXT UNIQUE NOT NULL,
                    display_name    TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gig_skills (
                    gig_id          INTEGER NOT NULL REFERENCES fiverr_gigs(id),
                    skill_id        INTEGER NOT NULL REFERENCES skills(id),
                    PRIMARY KEY (gig_id, skill_id)
                )
            """)

            # Indexes
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gig_skills_skill ON gig_skills(skill_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_upwork_scraped_at ON upwork_jobs(scraped_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_upwork_search ON upwork_jobs(search_query)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_upwork_category ON upwork_jobs(category)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_upwork_experience ON upwork_jobs(experience_level)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fiverr_scraped_at ON fiverr_gigs(scraped_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fiverr_search ON fiverr_gigs(search_query)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fiverr_category ON fiverr_gigs(category)")

            # Full-text search: tsvector trigger functions + GIN indexes
            cur.execute("""
                CREATE OR REPLACE FUNCTION upwork_search_vector_update() RETURNS trigger AS $$
                BEGIN
                    NEW.search_vector :=
                        setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
                        setweight(to_tsvector('english', COALESCE(NEW.skills, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(NEW.category, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'C');
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
            """)
            cur.execute("DROP TRIGGER IF EXISTS upwork_search_update ON upwork_jobs")
            cur.execute("""
                CREATE TRIGGER upwork_search_update
                    BEFORE INSERT OR UPDATE ON upwork_jobs
                    FOR EACH ROW EXECUTE FUNCTION upwork_search_vector_update()
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_upwork_search_vector ON upwork_jobs USING GIN(search_vector)")

            cur.execute("""
                CREATE OR REPLACE FUNCTION fiverr_search_vector_update() RETURNS trigger AS $$
                BEGIN
                    NEW.search_vector :=
                        setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
                        setweight(to_tsvector('english', COALESCE(NEW.tags, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(NEW.category, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(NEW.subcategory, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'C');
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
            """)
            cur.execute("DROP TRIGGER IF EXISTS fiverr_search_update ON fiverr_gigs")
            cur.execute("""
                CREATE TRIGGER fiverr_search_update
                    BEFORE INSERT OR UPDATE ON fiverr_gigs
                    FOR EACH ROW EXECUTE FUNCTION fiverr_search_vector_update()
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fiverr_search_vector ON fiverr_gigs USING GIN(search_vector)")

            # Analytics view: per-skill market stats
            cur.execute("""
                CREATE OR REPLACE VIEW skill_market_stats AS
                SELECT
                    s.id                                        AS skill_id,
                    s.display_name                              AS skill,
                    COUNT(DISTINCT gs.gig_id)                   AS gig_count,
                    ROUND(AVG(g.price_min)::numeric, 2)         AS avg_price_min,
                    ROUND(AVG(g.price_max)::numeric, 2)         AS avg_price_max,
                    ROUND(MIN(g.price_min)::numeric, 2)         AS floor_price,
                    ROUND(MAX(g.price_max)::numeric, 2)         AS ceiling_price,
                    ROUND(AVG(g.hourly_rate)::numeric, 2)       AS avg_hourly,
                    ROUND(AVG(g.rating)::numeric, 2)            AS avg_rating,
                    ROUND(AVG(g.num_reviews)::numeric, 0)       AS avg_reviews,
                    SUM(CASE WHEN g.seller_level LIKE 'Level 1%%' OR g.seller_level LIKE 'New%%'
                             THEN 1 ELSE 0 END)                 AS new_sellers,
                    SUM(CASE WHEN g.seller_level LIKE 'Level 2%%' OR g.seller_level LIKE 'Top%%'
                             THEN 1 ELSE 0 END)                 AS veteran_sellers,
                    ROUND((
                        CAST(SUM(CASE WHEN g.seller_level LIKE 'Level 1%%' OR g.seller_level LIKE 'New%%'
                                      THEN 1 ELSE 0 END) AS DOUBLE PRECISION)
                        / GREATEST(COUNT(DISTINCT gs.gig_id), 1) * 100
                    )::numeric, 1)                              AS pct_new_sellers,
                    STRING_AGG(DISTINCT g.category, ',')        AS categories
                FROM skills s
                JOIN gig_skills gs ON gs.skill_id = s.id
                JOIN fiverr_gigs g ON g.id = gs.gig_id
                GROUP BY s.id, s.display_name
                ORDER BY gig_count DESC
            """)

            # View: beginner-friendly opportunities (subquery to avoid HAVING alias issue)
            cur.execute("""
                CREATE OR REPLACE VIEW beginner_opportunities AS
                SELECT * FROM (
                    SELECT
                        s.display_name                              AS skill,
                        COUNT(DISTINCT gs.gig_id)                   AS gig_count,
                        ROUND(AVG(g.price_min)::numeric, 2)         AS avg_price,
                        ROUND((
                            CAST(SUM(CASE WHEN g.seller_level LIKE 'Level 1%%' OR g.seller_level LIKE 'New%%'
                                          THEN 1 ELSE 0 END) AS DOUBLE PRECISION)
                            / GREATEST(COUNT(DISTINCT gs.gig_id), 1) * 100
                        )::numeric, 1)                              AS pct_new_sellers,
                        ROUND(AVG(g.num_reviews)::numeric, 0)       AS avg_reviews,
                        ROUND(MIN(g.price_min)::numeric, 2)         AS entry_price
                    FROM skills s
                    JOIN gig_skills gs ON gs.skill_id = s.id
                    JOIN fiverr_gigs g ON g.id = gs.gig_id
                    GROUP BY s.id, s.display_name
                ) sub
                WHERE gig_count >= 3 AND pct_new_sellers >= 10
                ORDER BY avg_price DESC, pct_new_sellers DESC
            """)

            cur.execute(
                """INSERT INTO schema_meta (id, version) VALUES (1, %s)
                   ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version""",
                (SCHEMA_VERSION,),
            )

        self.conn.commit()
        self._populate_skills()

    def _populate_skills(self):
        """Parse JSON tags column into normalized skills + gig_skills. Resumable."""
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT g.id, g.tags FROM fiverr_gigs g
                   LEFT JOIN gig_skills gs ON gs.gig_id = g.id
                   WHERE g.tags IS NOT NULL AND g.tags != '' AND g.tags != '[]'
                   AND gs.gig_id IS NULL"""
            )
            rows = cur.fetchall()
        if not rows:
            return
        for row in rows:
            try:
                tags = json.loads(row["tags"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(tags, list):
                continue
            linked_any = False
            for tag in tags:
                canonical = _normalize_tag(tag)
                if canonical is None:
                    continue
                self._link_skill(row["id"], canonical)
                linked_any = True
            if not linked_any:
                with self.conn.cursor() as cur:
                    cur.execute("UPDATE fiverr_gigs SET tags = '[]' WHERE id = %s", (row["id"],))
        self.conn.commit()

    def _get_or_create_skill(self, display_name):
        name = display_name.lower()
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO skills (name, display_name) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                (name, display_name),
            )
            cur.execute("SELECT id FROM skills WHERE name = %s", (name,))
            return cur.fetchone()[0]

    def _link_skill(self, gig_id, display_name):
        skill_id = self._get_or_create_skill(display_name)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gig_skills (gig_id, skill_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (gig_id, skill_id),
            )

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def start_run(self, platform, query):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scrape_runs (platform, search_query, started_at) VALUES (%s, %s, %s) RETURNING id",
                (platform, query, self._now()),
            )
            run_id = cur.fetchone()[0]
        self.conn.commit()
        return run_id

    def finish_run(self, run_id, pages_scraped, jobs_found):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE scrape_runs SET finished_at=%s, pages_scraped=%s, jobs_found=%s WHERE id=%s",
                (self._now(), pages_scraped, jobs_found, run_id),
            )
        self.conn.commit()

    def insert_upwork_job(self, run_id, data):
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO upwork_jobs
                       (scrape_run_id, job_url, title, description, skills,
                        budget_type, budget_min, budget_max, experience_level,
                        client_rating, client_total_spent, client_hire_rate,
                        client_country, num_proposals, posted_date, category,
                        scraped_at, search_query)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (job_url) DO NOTHING""",
                    (
                        run_id,
                        data.get("job_url") or None,
                        data.get("title", ""),
                        data.get("description", ""),
                        json.dumps(data.get("skills") or []),
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
                inserted = cur.rowcount > 0
            self.conn.commit()
            return inserted
        except Exception:
            # Re-raise: returning False here would be indistinguishable from a
            # duplicate, and callers use that ratio to decide when to stop.
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise

    def insert_fiverr_gig(self, run_id, data):
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO fiverr_gigs
                       (scrape_run_id, gig_url, title, description,
                        seller_name, seller_level, seller_country,
                        seller_languages, seller_orders_completed,
                        price_min, price_max, hourly_rate, delivery_days,
                        num_reviews, rating, category, subcategory,
                        tags, scraped_at, search_query)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (gig_url) DO NOTHING
                       RETURNING id""",
                    (
                        run_id,
                        data.get("gig_url") or None,
                        data.get("title", ""),
                        data.get("description", ""),
                        data.get("seller_name", ""),
                        data.get("seller_level", ""),
                        data.get("seller_country", ""),
                        data.get("seller_languages", ""),
                        data.get("seller_orders_completed"),
                        data.get("price_min"),
                        data.get("price_max"),
                        data.get("hourly_rate"),
                        data.get("delivery_days"),
                        data.get("num_reviews"),
                        data.get("rating"),
                        data.get("category", ""),
                        data.get("subcategory", ""),
                        json.dumps(data.get("tags") or []),
                        self._now(),
                        data.get("search_query", ""),
                    ),
                )
                row = cur.fetchone()

            if row is None:
                return False

            gig_id = row[0]
            for tag in (data.get("tags") or []):
                canonical = _normalize_tag(tag)
                if canonical is not None:
                    self._link_skill(gig_id, canonical)

            self.conn.commit()
            return True
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise

    def get_upwork_jobs(self, query=None, limit=500):
        with self.conn.cursor() as cur:
            if query:
                cur.execute(
                    "SELECT * FROM upwork_jobs WHERE search_query=%s ORDER BY scraped_at DESC LIMIT %s",
                    (query, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM upwork_jobs ORDER BY scraped_at DESC LIMIT %s", (limit,)
                )
            return [dict(r) for r in cur.fetchall()]

    def get_fiverr_gigs(self, query=None, limit=500):
        with self.conn.cursor() as cur:
            if query:
                cur.execute(
                    "SELECT * FROM fiverr_gigs WHERE search_query=%s ORDER BY scraped_at DESC LIMIT %s",
                    (query, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM fiverr_gigs ORDER BY scraped_at DESC LIMIT %s", (limit,)
                )
            return [dict(r) for r in cur.fetchall()]

    def search_text(self, platform, query):
        query = query.strip()
        if not query:
            return []
        safe_query = re.sub(r'[^\w\s]', ' ', query).strip()
        safe_query = re.sub(r'\s+', ' ', safe_query).strip()
        if not safe_query:
            return []
        try:
            with self.conn.cursor() as cur:
                if platform == "upwork":
                    cur.execute(
                        """SELECT u.* FROM upwork_jobs u
                           WHERE u.search_vector @@ plainto_tsquery('english', %s)
                           ORDER BY ts_rank(u.search_vector, plainto_tsquery('english', %s)) DESC
                           LIMIT 50""",
                        (safe_query, safe_query),
                    )
                else:
                    cur.execute(
                        """SELECT g.* FROM fiverr_gigs g
                           WHERE g.search_vector @@ plainto_tsquery('english', %s)
                           ORDER BY ts_rank(g.search_vector, plainto_tsquery('english', %s)) DESC
                           LIMIT 50""",
                        (safe_query, safe_query),
                    )
                return [dict(r) for r in cur.fetchall()]
        except psycopg2.Error:
            self.conn.rollback()
            return []

    def get_skill_stats(self, min_gigs=3):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM skill_market_stats WHERE gig_count >= %s ORDER BY gig_count DESC",
                (min_gigs,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_beginner_opportunities(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM beginner_opportunities")
            return [dict(r) for r in cur.fetchall()]

    def get_high_value_skills(self, min_gigs=3, min_avg_price=50):
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM skill_market_stats
                   WHERE gig_count >= %s AND avg_price_min >= %s
                   ORDER BY avg_price_min DESC""",
                (min_gigs, min_avg_price),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_skills_for_gig(self, gig_id):
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT s.display_name FROM skills s
                   JOIN gig_skills gs ON gs.skill_id = s.id
                   WHERE gs.gig_id = %s""",
                (gig_id,),
            )
            return [r["display_name"] for r in cur.fetchall()]

    def get_stats(self):
        stats = {}
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM upwork_jobs")
            stats["upwork_total"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM fiverr_gigs")
            stats["fiverr_total"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM scrape_runs")
            stats["total_runs"] = cur.fetchone()[0]

            cur.execute("SELECT MIN(scraped_at), MAX(scraped_at) FROM upwork_jobs")
            row = cur.fetchone()
            stats["upwork_first"] = row[0] or "N/A"
            stats["upwork_last"] = row[1] or "N/A"

            cur.execute("SELECT MIN(scraped_at), MAX(scraped_at) FROM fiverr_gigs")
            row = cur.fetchone()
            stats["fiverr_first"] = row[0] or "N/A"
            stats["fiverr_last"] = row[1] or "N/A"

            cur.execute("SELECT DISTINCT search_query FROM upwork_jobs WHERE search_query IS NOT NULL AND search_query != '' ORDER BY search_query")
            stats["upwork_queries"] = [r[0] for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT search_query FROM fiverr_gigs WHERE search_query IS NOT NULL AND search_query != '' ORDER BY search_query")
            stats["fiverr_queries"] = [r[0] for r in cur.fetchall()]

        return stats

    def close(self):
        self.conn.close()
