import json
import re

import psycopg2
import psycopg2.extras
import chromadb

from config import DATABASE_URL, CHROMA_DIR


class RAGEngine:
    def __init__(self):
        self.db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
        self.db.autocommit = True
        self.chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))

        # ChromaDB for Fiverr semantic search (small dataset, ~2.5K docs)
        self.fiverr_collection = self.chroma.get_or_create_collection(
            name="gigs",
            metadata={"hnsw:space": "cosine"},
        )
        try:
            cur = self.db.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM fiverr_gigs WHERE description IS NOT NULL AND description != ''"
            )
            db_count = cur.fetchone()[0]
        except psycopg2.Error:
            print("Warning: Database not initialized. Run the scraper first.")
            db_count = None
        if db_count is not None and self.fiverr_collection.count() != db_count:
            self.chroma.delete_collection("gigs")
            self.fiverr_collection = self.chroma.get_or_create_collection(
                name="gigs",
                metadata={"hnsw:space": "cosine"},
            )
            self._index_fiverr()

        # Upwork uses PostgreSQL full-text search (tsvector + GIN index)

    # ---- Fiverr indexing (ChromaDB) ----

    def _index_fiverr(self):
        cur = self.db.cursor()
        cur.execute("""
            SELECT id, title, description, seller_level, seller_country,
                   price_min, price_max, category, subcategory, tags,
                   num_reviews, rating
            FROM fiverr_gigs
            WHERE description IS NOT NULL AND description != ''
        """)
        rows = cur.fetchall()

        print(f"Indexing {len(rows)} Fiverr gigs into vector DB...")
        batch_size = 100
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            ids, documents, metadatas = [], [], []
            for row in batch:
                doc = f"{row['title']}\n\n{row['description']}"
                try:
                    tags = json.loads(row["tags"]) if row["tags"] else []
                except (json.JSONDecodeError, TypeError):
                    tags = []
                if tags:
                    tags = [t for t in tags if isinstance(t, str)]
                    if tags:
                        doc += f"\n\nSkills: {', '.join(tags)}"

                ids.append(str(row["id"]))
                documents.append(doc)
                metadatas.append({
                    "gig_id": row["id"],
                    "title": row["title"] or "",
                    "seller_level": row["seller_level"] or "",
                    "seller_country": row["seller_country"] or "",
                    "price_min": float(row["price_min"]) if row["price_min"] else 0,
                    "price_max": float(row["price_max"]) if row["price_max"] else 0,
                    "category": row["category"] or "",
                    "num_reviews": int(row["num_reviews"]) if row["num_reviews"] else 0,
                    "rating": float(row["rating"]) if row["rating"] else 0,
                })

            self.fiverr_collection.add(ids=ids, documents=documents, metadatas=metadatas)
            print(f"  Fiverr: {min(i + batch_size, len(rows))}/{len(rows)}")

        print("Fiverr indexing complete.")

    # ---- Fiverr search (semantic via ChromaDB) ----

    def search_gigs(self, query, n_results=8):
        results = self.fiverr_collection.query(query_texts=[query], n_results=n_results)
        gigs = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            dist = results["distances"][0][i] if results.get("distances") else None
            gigs.append({
                "document": doc[:500],
                "metadata": meta,
                "relevance": round(max(0, 1 - dist), 3) if dist is not None else None,
            })
        return gigs

    # ---- Upwork search (PostgreSQL full-text search) ----

    def search_upwork_jobs(self, query, n_results=8):
        safe_query = re.sub(r'[^\w\s]', ' ', query).strip()
        safe_query = re.sub(r'\s+', ' ', safe_query).strip()
        if not safe_query:
            return []
        try:
            cur = self.db.cursor()
            cur.execute(
                """SELECT u.id, u.title, u.description, u.skills,
                          u.budget_type, u.budget_min, u.budget_max,
                          u.experience_level, u.search_query
                   FROM upwork_jobs u
                   WHERE u.search_vector @@ plainto_tsquery('english', %s)
                   ORDER BY ts_rank(u.search_vector, plainto_tsquery('english', %s)) DESC
                   LIMIT %s""",
                (safe_query, safe_query, n_results),
            )
            return [dict(r) for r in cur.fetchall()]
        except psycopg2.Error:
            try:
                self.db.rollback()
            except Exception:
                pass
            return []

    # ---- Fiverr analytics ----

    def get_skill_stats(self, min_gigs=3):
        cur = self.db.cursor()
        cur.execute(
            "SELECT * FROM skill_market_stats WHERE gig_count >= %s ORDER BY avg_price_min DESC",
            (min_gigs,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_beginner_opportunities(self):
        cur = self.db.cursor()
        cur.execute("SELECT * FROM beginner_opportunities")
        return [dict(r) for r in cur.fetchall()]

    def get_high_value_skills(self, min_gigs=3, min_price=50):
        cur = self.db.cursor()
        cur.execute(
            """SELECT * FROM skill_market_stats
               WHERE gig_count >= %s AND avg_price_min >= %s
               ORDER BY avg_price_min DESC""",
            (min_gigs, min_price),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_saturated_low_value(self, min_gigs=10):
        cur = self.db.cursor()
        cur.execute(
            """SELECT * FROM skill_market_stats
               WHERE gig_count >= %s AND avg_price_min < 30
               ORDER BY gig_count DESC""",
            (min_gigs,),
        )
        return [dict(r) for r in cur.fetchall()]

    # ---- Upwork analytics ----

    def get_upwork_skill_demand(self, top_n=25):
        cur = self.db.cursor()
        cur.execute(
            "SELECT skills FROM upwork_jobs WHERE skills IS NOT NULL AND skills != '[]'"
        )
        rows = cur.fetchall()
        counts = {}
        for row in rows:
            try:
                for s in json.loads(row["skills"]):
                    if not isinstance(s, str):
                        continue
                    t = s.strip()
                    if t and len(t) > 1:
                        counts[t] = counts.get(t, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(counts.items(), key=lambda x: -x[1])[:top_n]

    def get_upwork_experience_demand(self):
        cur = self.db.cursor()
        cur.execute(
            """SELECT experience_level, COUNT(*) as c
               FROM upwork_jobs WHERE experience_level != ''
               GROUP BY experience_level ORDER BY c DESC"""
        )
        return [{"level": r["experience_level"], "count": r["c"]} for r in cur.fetchall()]

    def get_upwork_budget_stats(self):
        cur = self.db.cursor()
        cur.execute(
            """SELECT budget_type, COUNT(*) as c,
                      AVG(budget_min) as avg_min, AVG(budget_max) as avg_max
               FROM upwork_jobs WHERE budget_type != ''
               GROUP BY budget_type ORDER BY c DESC"""
        )
        return [{
            "type": r["budget_type"],
            "count": r["c"],
            "avg_min": r["avg_min"] or 0,
            "avg_max": r["avg_max"] or 0,
        } for r in cur.fetchall()]

    # ---- Context builder ----

    def build_context(self, question):
        parts = []

        # --- SUPPLY SIDE: Fiverr gigs (semantic search) ---
        gigs = self.search_gigs(question, n_results=6)
        if gigs:
            lines = []
            for g in gigs:
                m = g["metadata"]
                price = f"${m['price_min']:.0f}"
                if m.get("price_max"):
                    price += f"-${m['price_max']:.0f}"
                relevance = f"{g['relevance']:.0%}" if g['relevance'] is not None else "?"
                lines.append(
                    f"  - {m['title'][:80]} | {m['seller_level'] or 'Unknown'} | "
                    f"{m['seller_country'] or '?'} | {price} | "
                    f"{m['num_reviews']} reviews | {relevance} match"
                )
            parts.append("SUPPLY: RELEVANT FIVERR GIGS (what freelancers offer):\n" + "\n".join(lines))

        # --- DEMAND SIDE: Upwork jobs (full-text search) ---
        upwork_jobs = self.search_upwork_jobs(question, n_results=6)
        if upwork_jobs:
            lines = []
            for j in upwork_jobs:
                budget = ""
                if j.get("budget_min") is not None:
                    budget = f"${j['budget_min']:.0f}"
                    if j.get("budget_max") is not None:
                        budget += f"-${j['budget_max']:.0f}"
                skills = ""
                try:
                    skill_list = json.loads(j.get("skills", "[]"))
                    if skill_list:
                        skills = ", ".join(skill_list[:3])
                except (json.JSONDecodeError, TypeError):
                    pass
                lines.append(
                    f"  - {j['title'][:80]} | {j['budget_type']} "
                    f"{budget or 'N/A'} | {j['experience_level'] or '?'}"
                    f"{' | ' + skills if skills else ''}"
                )
            parts.append("DEMAND: RELEVANT UPWORK JOBS (what clients hire for):\n" + "\n".join(lines))

        # --- Fiverr supply analytics ---
        try:
            high = self.get_high_value_skills(min_gigs=3, min_price=50)[:20]
            if high:
                lines = [
                    f"  - {s['skill']}: {s['gig_count']} gigs, avg ${s['avg_price_min']:.0f}, "
                    f"{s['pct_new_sellers']:.0f}% new sellers, avg rating "
                    + (f"{s['avg_rating']:.1f}" if s['avg_rating'] is not None else "N/A")
                    for s in high
                ]
                parts.append("SUPPLY: HIGH-VALUE SKILLS on Fiverr (avg >= $50, 3+ gigs):\n" + "\n".join(lines))

            beginner = self.get_beginner_opportunities()[:20]
            if beginner:
                lines = [
                    f"  - {s['skill']}: {s['gig_count']} gigs, avg "
                    + (f"${s['avg_price']:.0f}" if s['avg_price'] is not None else "N/A")
                    + f", {s['pct_new_sellers']:.0f}% new sellers, entry "
                    + (f"${s['entry_price']:.0f}" if s['entry_price'] is not None else "N/A")
                    for s in beginner
                ]
                parts.append("SUPPLY: BEGINNER-FRIENDLY SKILLS on Fiverr:\n" + "\n".join(lines))

            saturated = self.get_saturated_low_value(min_gigs=10)[:20]
            if saturated:
                lines = [
                    f"  - {s['skill']}: {s['gig_count']} gigs, avg ${s['avg_price_min']:.0f}, "
                    f"{s['pct_new_sellers']:.0f}% new sellers"
                    for s in saturated
                ]
                parts.append("SUPPLY: SATURATED LOW-VALUE SKILLS on Fiverr (avg < $30, 10+ gigs):\n" + "\n".join(lines))
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass

        # --- Upwork demand analytics ---
        try:
            top_skills = self.get_upwork_skill_demand(top_n=20)
            if top_skills:
                lines = [f"  - {s}: {c:,} job postings" for s, c in top_skills]
                parts.append("DEMAND: TOP SKILLS CLIENTS HIRE FOR on Upwork:\n" + "\n".join(lines))

            exp = self.get_upwork_experience_demand()
            if exp:
                lines = [f"  - {e['level']}: {e['count']:,} jobs" for e in exp]
                parts.append("DEMAND: EXPERIENCE LEVELS CLIENTS WANT:\n" + "\n".join(lines))

            budgets = self.get_upwork_budget_stats()
            if budgets:
                lines = []
                for b in budgets:
                    avg_max = f"${b['avg_max']:.0f}" if b["avg_max"] else "N/A"
                    lines.append(f"  - {b['type']}: {b['count']:,} jobs, avg ${b['avg_min']:.0f}-{avg_max}")
                parts.append("DEMAND: BUDGET DISTRIBUTION on Upwork:\n" + "\n".join(lines))
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass

        # --- Market overview ---
        try:
            cur = self.db.cursor()
            cur.execute("SELECT COUNT(*) FROM fiverr_gigs")
            fiverr_total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM skills")
            fiverr_skills = cur.fetchone()[0]
            cur.execute("SELECT AVG(price_min) FROM fiverr_gigs")
            fiverr_avg = cur.fetchone()[0] or 0

            cur.execute("SELECT COUNT(*) FROM upwork_jobs")
            upwork_total = cur.fetchone()[0]
            cur.execute(
                "SELECT AVG(budget_min) FROM upwork_jobs WHERE budget_type='Hourly' AND budget_min > 0"
            )
            upwork_avg_hourly = cur.fetchone()[0] or 0
            cur.execute(
                "SELECT AVG(budget_min) FROM upwork_jobs WHERE budget_type='Fixed' AND budget_min > 0"
            )
            upwork_avg_fixed = cur.fetchone()[0] or 0

            parts.append(
                f"MARKET OVERVIEW:\n"
                f"  Supply (Fiverr): {fiverr_total:,} gigs, {fiverr_skills} distinct skills, "
                f"avg starting price ${float(fiverr_avg):.0f}\n"
                f"  Demand (Upwork): {upwork_total:,} job postings, "
                f"avg hourly ${float(upwork_avg_hourly):.0f}/hr, avg fixed ${float(upwork_avg_fixed):.0f}"
            )
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass

        return "\n\n".join(parts)

    # ---- Backward compat ----

    @property
    def collection(self):
        return self.fiverr_collection

    def close(self):
        self.db.close()
