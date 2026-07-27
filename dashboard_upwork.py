import decimal
import html as html_mod
import http.server
import json

import psycopg2
import psycopg2.extras


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        return super().default(o)

from config import DATABASE_URL

PORT = 8051

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Upwork Scraper Dashboard</title>
<meta http-equiv="refresh" content="15">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e0e0e0; padding: 24px; }
  h1 { font-size: 22px; margin-bottom: 20px; color: #fff; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .card { background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 10px; padding: 20px; }
  .card h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 8px; }
  .card .big { font-size: 42px; font-weight: 700; color: #66bb6a; }
  .card .sub { font-size: 13px; color: #888; margin-top: 4px; }
  .progress-wrap { background: #2a2d3a; border-radius: 8px; height: 32px; overflow: hidden; margin-bottom: 24px; }
  .progress-bar { height: 100%; background: linear-gradient(90deg, #66bb6a, #43a047); border-radius: 8px;
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
  .bar-mini { display: inline-block; height: 8px; border-radius: 4px; background: #66bb6a; }
</style>
</head>
<body>
<h1>Upwork Job Scraper — Live Dashboard</h1>
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

    cur.execute("SELECT COUNT(*) FROM upwork_jobs")
    s["total"] = cur.fetchone()[0]
    s["target"] = 90000
    s["pct"] = min(100, s["total"] / max(s["target"], 1) * 100)

    # Per-query breakdown
    s["queries"] = []
    cur.execute(
        "SELECT search_query, COUNT(*) as c, AVG(budget_min) as avg_budget, "
        "SUM(CASE WHEN budget_type='Fixed' THEN 1 ELSE 0 END) as fixed_count, "
        "SUM(CASE WHEN budget_type='Hourly' THEN 1 ELSE 0 END) as hourly_count "
        "FROM upwork_jobs GROUP BY search_query ORDER BY c DESC"
    )
    for row in cur.fetchall():
        s["queries"].append({
            "query": row["search_query"],
            "count": row["c"],
            "avg_budget": row["avg_budget"] or 0,
            "fixed": row["fixed_count"] or 0,
            "hourly": row["hourly_count"] or 0,
        })

    # Experience level distribution
    s["experience"] = []
    cur.execute(
        "SELECT experience_level, COUNT(*) as c FROM upwork_jobs "
        "WHERE experience_level != '' GROUP BY experience_level ORDER BY c DESC"
    )
    for row in cur.fetchall():
        s["experience"].append({"level": row["experience_level"], "count": row["c"]})

    # Budget type distribution
    s["budget_types"] = []
    cur.execute(
        "SELECT budget_type, COUNT(*) as c, AVG(budget_min) as avg_min, AVG(budget_max) as avg_max "
        "FROM upwork_jobs WHERE budget_type != '' GROUP BY budget_type ORDER BY c DESC"
    )
    for row in cur.fetchall():
        s["budget_types"].append({
            "type": row["budget_type"],
            "count": row["c"],
            "avg_min": row["avg_min"] or 0,
            "avg_max": row["avg_max"] or 0,
        })

    # Top skills
    skill_counts = {}
    cur.execute("SELECT skills FROM upwork_jobs WHERE skills IS NOT NULL AND skills != '[]'")
    for row in cur.fetchall():
        try:
            for skill in json.loads(row["skills"]):
                if not isinstance(skill, str):
                    continue
                t = skill.strip()
                if t and len(t) > 1:
                    skill_counts[t] = skill_counts.get(t, 0) + 1
        except (json.JSONDecodeError, TypeError):
            pass
    s["total_unique_skills"] = len(skill_counts)
    s["top_skills"] = sorted(skill_counts.items(), key=lambda x: -x[1])[:15]

    # Recent jobs
    s["recent"] = []
    cur.execute(
        "SELECT title, budget_type, budget_min, budget_max, experience_level, search_query "
        "FROM upwork_jobs ORDER BY id DESC LIMIT 8"
    )
    for row in cur.fetchall():
        budget = ""
        if row["budget_min"] is not None:
            budget = f"${row['budget_min']:.0f}"
            if row["budget_max"] is not None:
                budget += f"-${row['budget_max']:.0f}"
        s["recent"].append({
            "title": row["title"],
            "budget": budget or "N/A",
            "experience": row["experience_level"] or "?",
            "query": row["search_query"],
        })

    # Active run
    cur.execute(
        "SELECT * FROM scrape_runs WHERE platform='upwork' ORDER BY id DESC LIMIT 1"
    )
    run = cur.fetchone()
    if run:
        s["last_run"] = {
            "query": run["search_query"],
            "jobs": run["jobs_found"],
            "finished": run["finished_at"] is not None,
        }
    else:
        s["last_run"] = None

    # Description stats
    cur.execute(
        "SELECT AVG(LENGTH(description)) as avg_len, MIN(LENGTH(description)) as min_len, "
        "MAX(LENGTH(description)) as max_len FROM upwork_jobs WHERE description != ''"
    )
    ds = cur.fetchone()
    s["desc_avg"] = int(ds["avg_len"] or 0)
    s["desc_min"] = ds["min_len"] or 0
    s["desc_max"] = ds["max_len"] or 0

    # Fill rates
    s["fill"] = {}
    for col in ["description", "skills", "budget_type", "experience_level", "posted_date"]:
        if col == "skills":
            cur.execute(
                "SELECT COUNT(*) FROM upwork_jobs WHERE skills IS NOT NULL AND skills != '' AND skills != '[]'"
            )
        else:
            cur.execute(
                f"SELECT COUNT(*) FROM upwork_jobs WHERE {col} IS NOT NULL AND {col} != ''"
            )
        s["fill"][col] = cur.fetchone()[0]

    return s


def render(s):
    pct = s["pct"]
    bar_color = "#4caf50" if pct >= 100 else "#66bb6a"
    esc = html_mod.escape

    out = f"""
    <div class="progress-wrap">
      <div class="progress-bar" style="width:{max(6, pct):.1f}%; background:linear-gradient(90deg, {bar_color}, {bar_color});">
        {s['total']} / {s['target']} ({pct:.1f}%)
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Total Jobs</h2>
        <div class="big">{s['total']}</div>
        <div class="sub">Target: {s['target']}</div>
      </div>
      <div class="card">
        <h2>Search Queries Scraped</h2>
        <div class="big">{len(s['queries'])}</div>
        <div class="sub">~50 jobs per page</div>
      </div>
      <div class="card">
        <h2>Description Quality</h2>
        <div class="big">{s['desc_avg']}</div>
        <div class="sub">avg chars (range {s['desc_min']}-{s['desc_max']})</div>
      </div>
      <div class="card">
        <h2>Unique Skills Found</h2>
        <div class="big">{s.get('total_unique_skills', 0)}</div>
        <div class="sub">top {len(s.get('top_skills', []))} shown below</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Queries Breakdown</h2>
        <table>
          <tr><th>Search Query</th><th>Jobs</th><th>Avg Budget</th><th>Fixed</th><th>Hourly</th></tr>
    """
    for q in s["queries"]:
        out += f"""<tr>
          <td>{esc(q['query'] or '')}</td><td>{q['count']}</td>
          <td>${q['avg_budget']:.0f}</td><td>{q['fixed']}</td><td>{q['hourly']}</td>
        </tr>"""

    out += """</table></div><div class="card"><h2>Experience Levels</h2><table>
      <tr><th>Level</th><th>Jobs</th><th></th></tr>"""
    max_exp = s["experience"][0]["count"] if s["experience"] else 1
    for e in s["experience"]:
        bar_w = e["count"] / max_exp * 100
        out += f"""<tr><td>{esc(e['level'])}</td><td>{e['count']}</td>
          <td><span class="bar-mini" style="width:{bar_w}%"></span></td></tr>"""

    out += """</table></div></div>

    <div class="grid">
      <div class="card">
        <h2>Top Skills in Demand</h2>
        <table><tr><th>Skill</th><th>Appearances</th><th></th></tr>"""
    max_skill = s["top_skills"][0][1] if s["top_skills"] else 1
    for skill, cnt in s["top_skills"]:
        bar_w = cnt / max_skill * 100
        out += f"""<tr><td>{esc(skill)}</td><td>{cnt}</td>
          <td><span class="bar-mini" style="width:{bar_w}%"></span></td></tr>"""

    out += """</table></div>
      <div class="card">
        <h2>Recent Jobs</h2>
        <table><tr><th>Title</th><th>Budget</th><th>Level</th></tr>"""
    for r in s["recent"]:
        title_short = r["title"][:55] + "..." if len(r["title"]) > 55 else r["title"]
        out += f"""<tr>
          <td class="recent-title" title="{esc(r['title'])}">{esc(title_short)}</td>
          <td>{esc(r['budget'])}</td><td>{esc(r['experience'])}</td></tr>"""

    out += """</table></div></div>

    <div class="grid">
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
        <h2>Budget Types</h2>
        <table><tr><th>Type</th><th>Jobs</th><th>Avg Min</th><th>Avg Max</th></tr>"""
    for b in s.get("budget_types", []):
        avg_max = f"${b['avg_max']:.0f}" if b["avg_max"] else "—"
        out += f"""<tr><td>{esc(b['type'])}</td><td>{b['count']}</td>
          <td>${b['avg_min']:.0f}</td><td>{avg_max}</td></tr>"""

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
                stats["top_skills"] = [{"skill": t, "count": c} for t, c in stats["top_skills"]]
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
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Upwork Dashboard running at http://localhost:{PORT}")
    server.serve_forever()
