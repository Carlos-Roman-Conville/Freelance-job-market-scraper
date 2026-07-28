import decimal
import html as html_mod
import http.server
import json
import threading
import time

import psycopg2
import psycopg2.extras


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        return super().default(o)

from config import DATABASE_URL

PORT = 8050

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Scraper Dashboard</title>
<meta http-equiv="refresh" content="15">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e0e0e0; padding: 24px; }
  h1 { font-size: 22px; margin-bottom: 20px; color: #fff; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .card { background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 10px; padding: 20px; }
  .card h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 8px; }
  .card .big { font-size: 42px; font-weight: 700; color: #4fc3f7; }
  .card .sub { font-size: 13px; color: #888; margin-top: 4px; }
  .progress-wrap { background: #2a2d3a; border-radius: 8px; height: 32px; overflow: hidden; margin-bottom: 24px; }
  .progress-bar { height: 100%; background: linear-gradient(90deg, #4fc3f7, #29b6f6); border-radius: 8px;
                   display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 14px;
                   transition: width 0.5s ease; min-width: 60px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #888;
       padding: 8px 12px; border-bottom: 1px solid #2a2d3a; }
  td { padding: 8px 12px; border-bottom: 1px solid #1e2130; font-size: 13px; }
  tr:hover td { background: #1e2130; }
  .tag { display: inline-block; background: #2a2d3a; border-radius: 4px; padding: 2px 8px;
         font-size: 11px; margin: 1px 2px; color: #aaa; }
  .status-running { color: #4caf50; }
  .status-done { color: #888; }
  .full { grid-column: 1 / -1; }
  .recent-title { max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-mini { display: inline-block; height: 8px; border-radius: 4px; background: #4fc3f7; }
</style>
</head>
<body>
<h1>Freelance Market Scraper — Live Dashboard</h1>
%%CONTENT%%
<p style="color:#555; font-size:11px; margin-top:16px;">Auto-refreshes every 15s</p>
</body>
</html>"""


def get_stats():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
    try:
        return _get_stats(conn)
    finally:
        conn.close()


def _get_stats(conn):
    s = {}
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM fiverr_gigs")
    s["total"] = cur.fetchone()[0]
    s["target"] = 1000
    s["pct"] = min(100, s["total"] / s["target"] * 100)

    # Per-query breakdown
    s["queries"] = []
    cur.execute(
        "SELECT search_query, COUNT(*) as c, AVG(price_min) as avg_price, "
        "AVG(rating) as avg_rating, AVG(num_reviews) as avg_reviews "
        "FROM fiverr_gigs GROUP BY search_query ORDER BY c DESC"
    )
    for row in cur.fetchall():
        s["queries"].append({
            "query": row["search_query"],
            "count": row["c"],
            "avg_price": row["avg_price"] or 0,
            "avg_rating": row["avg_rating"] or 0,
            "avg_reviews": row["avg_reviews"] or 0,
        })

    # Country distribution
    s["countries"] = []
    cur.execute(
        "SELECT seller_country, COUNT(*) as c FROM fiverr_gigs "
        "WHERE seller_country != '' GROUP BY seller_country ORDER BY c DESC LIMIT 10"
    )
    for row in cur.fetchall():
        s["countries"].append({"country": row["seller_country"], "count": row["c"]})

    # Category distribution
    s["categories"] = []
    cur.execute(
        "SELECT category, subcategory, COUNT(*) as c FROM fiverr_gigs "
        "GROUP BY category, subcategory ORDER BY c DESC LIMIT 10"
    )
    for row in cur.fetchall():
        s["categories"].append({
            "cat": row["category"], "sub": row["subcategory"], "count": row["c"]
        })

    # Top skills (from normalized skills table)
    cur.execute("SELECT COUNT(*) FROM skills")
    s["total_unique_tags"] = cur.fetchone()[0]
    cur.execute(
        """SELECT s.display_name, COUNT(*) as c FROM skills s
           JOIN gig_skills gs ON gs.skill_id = s.id
           GROUP BY s.id, s.display_name ORDER BY c DESC LIMIT 15"""
    )
    s["top_tags"] = [(row[0], row[1]) for row in cur.fetchall()]

    # Recent gigs
    s["recent"] = []
    cur.execute(
        "SELECT title, seller_country, price_min, price_max, rating, num_reviews, search_query "
        "FROM fiverr_gigs ORDER BY id DESC LIMIT 8"
    )
    for row in cur.fetchall():
        s["recent"].append({
            "title": row["title"],
            "country": row["seller_country"],
            "price": (f"${row['price_min']:.0f}" if row["price_min"] is not None else "N/A") + (f"-${row['price_max']:.0f}" if row["price_min"] is not None and row["price_max"] else ""),
            "rating": row["rating"],
            "reviews": row["num_reviews"],
            "query": row["search_query"],
        })

    # Active run
    cur.execute("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1")
    run = cur.fetchone()
    if run:
        s["last_run"] = {
            "query": run["search_query"],
            "jobs": run["jobs_found"],
            "finished": run["finished_at"] is not None,
        }
    else:
        s["last_run"] = None

    # Desc stats
    cur.execute(
        "SELECT AVG(LENGTH(description)) as avg_len, MIN(LENGTH(description)) as min_len, "
        "MAX(LENGTH(description)) as max_len FROM fiverr_gigs WHERE description != ''"
    )
    ds = cur.fetchone()
    s["desc_avg"] = int(ds["avg_len"] or 0)
    s["desc_min"] = ds["min_len"] or 0
    s["desc_max"] = ds["max_len"] or 0

    # Fill rates
    s["fill"] = {}
    for col in ["description", "tags", "seller_country", "seller_languages", "hourly_rate"]:
        if col == "tags":
            cur.execute(
                "SELECT COUNT(*) FROM fiverr_gigs WHERE tags IS NOT NULL AND tags != '' AND tags != '[]'"
            )
        elif col in ("hourly_rate",):
            cur.execute(
                f"SELECT COUNT(*) FROM fiverr_gigs WHERE {col} IS NOT NULL AND {col} > 0"
            )
        else:
            cur.execute(
                f"SELECT COUNT(*) FROM fiverr_gigs WHERE {col} IS NOT NULL AND {col} != ''"
            )
        s["fill"][col] = cur.fetchone()[0]

    return s


def render(s):
    pct = s["pct"]
    bar_color = "#4caf50" if pct >= 100 else "#4fc3f7"

    out = f"""
    <div class="progress-wrap">
      <div class="progress-bar" style="width:{max(6, pct):.1f}%; background:linear-gradient(90deg, {bar_color}, {bar_color});">
        {s['total']} / {s['target']} ({pct:.1f}%)
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Total Gigs</h2>
        <div class="big">{s['total']}</div>
        <div class="sub">Target: {s['target']}</div>
      </div>
      <div class="card">
        <h2>Search Queries Scraped</h2>
        <div class="big">{len(s['queries'])}</div>
        <div class="sub">~50 gigs per query</div>
      </div>
      <div class="card">
        <h2>Description Quality</h2>
        <div class="big">{s['desc_avg']}</div>
        <div class="sub">avg chars (range {s['desc_min']}-{s['desc_max']})</div>
      </div>
      <div class="card">
        <h2>Unique Skills Found</h2>
        <div class="big">{s.get('total_unique_tags', 0)}</div>
        <div class="sub">top {len(s.get('top_tags', []))} shown below</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Queries Breakdown</h2>
        <table>
          <tr><th>Search Query</th><th>Gigs</th><th>Avg $</th><th>Avg Rating</th></tr>
    """
    esc = html_mod.escape
    for q in s["queries"]:
        out += f"""<tr>
          <td>{esc(q['query'] or '')}</td><td>{q['count']}</td>
          <td>${q['avg_price']:.0f}</td><td>{q['avg_rating']:.1f}</td>
        </tr>"""

    out += """</table></div><div class="card"><h2>Top Countries</h2><table>
      <tr><th>Country</th><th>Sellers</th><th></th></tr>"""
    max_country = s["countries"][0]["count"] if s["countries"] else 1
    for c in s["countries"]:
        bar_w = c["count"] / max_country * 100
        out += f"""<tr><td>{esc(c['country'])}</td><td>{c['count']}</td>
          <td><span class="bar-mini" style="width:{bar_w}%"></span></td></tr>"""

    out += """</table></div></div>

    <div class="grid">
      <div class="card">
        <h2>Top Skills / Tags</h2>
        <table><tr><th>Skill</th><th>Appearances</th><th></th></tr>"""
    max_tag = s["top_tags"][0][1] if s["top_tags"] else 1
    for tag, cnt in s["top_tags"]:
        bar_w = cnt / max_tag * 100
        out += f"""<tr><td>{esc(tag)}</td><td>{cnt}</td>
          <td><span class="bar-mini" style="width:{bar_w}%"></span></td></tr>"""

    out += """</table></div>
      <div class="card">
        <h2>Recent Gigs</h2>
        <table><tr><th>Title</th><th>Price</th><th>Country</th></tr>"""
    for r in s["recent"]:
        title_short = r["title"][:55] + "..." if len(r["title"]) > 55 else r["title"]
        out += f"""<tr>
          <td class="recent-title" title="{esc(r['title'])}">{esc(title_short)}</td>
          <td>{esc(r['price'])}</td><td>{esc(r['country'] or '')}</td></tr>"""

    out += """</table></div></div>"""

    out += """<div class="grid">
      <div class="card">
        <h2>Data Completeness</h2>
        <table><tr><th>Field</th><th>Filled</th><th>Rate</th></tr>"""
    total = s["total"] or 1
    for col, filled in s.get("fill", {}).items():
        rate = filled / total * 100
        label = col.replace("_", " ").title()
        out += f"""<tr><td>{esc(label)}</td><td>{filled}</td><td>{rate:.0f}%</td></tr>"""

    out += """</table></div>
      <div class="card">
        <h2>Top Categories</h2>
        <table><tr><th>Category</th><th>Subcategory</th><th>Gigs</th></tr>"""
    for c in s.get("categories", []):
        out += f"""<tr><td>{esc(c.get('cat', '') or '')}</td>
          <td>{esc(c.get('sub', '') or '')}</td><td>{c['count']}</td></tr>"""

    out += "</table></div></div>"
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            try:
                stats = get_stats()
                content = render(stats)
                page = HTML.replace("%%CONTENT%%", content)
            except Exception:
                page = HTML.replace("%%CONTENT%%", "<p>Error loading dashboard data.</p>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())
        elif self.path == "/api/stats":
            try:
                stats = get_stats()
                stats["top_tags"] = [{"tag": t, "count": c} for t, c in stats["top_tags"]]
                data = json.dumps(stats, cls=_DecimalEncoder)
                self.send_response(200)
            except Exception:
                data = json.dumps({"error": "Internal server error"})
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    # Threading matters here: the page auto-refreshes every 15s, so a couple of
    # open tabs keep a single-threaded server permanently saturated and every
    # viewer queues behind the others.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard running at http://localhost:{PORT}")
    server.serve_forever()
