# Freelance Job Market Scraper + AI Career Agent

A data pipeline that scrapes 1000+ freelance gigs from Fiverr, normalizes the data into a structured SQLite database with per-skill market analytics, and powers a RAG-based AI chatbot that gives actionable career intelligence.

**Ask it questions like:**
- "What skills should I learn as a beginner?"
- "Is Python freelancing worth getting into?"
- "What niches are oversaturated and low-paying?"
- "What projects should I build to get my first client?"

The agent responds with specific numbers — gig counts, average prices, competition levels, entry prices — not generic advice.

## Architecture

```
Fiverr Search Results
        |
        v
  nodriver (stealth browser automation)
        |
        v
  Gig Detail Pages  -->  JSON-LD + HTML parsing
        |
        v
  SQLite Database
    - fiverr_gigs (raw data)
    - skills / gig_skills (normalized junction table)
    - FTS5 full-text search index
    - SQL views: skill_market_stats, beginner_opportunities
        |
        v
  RAG Engine
    - ChromaDB (semantic vector search over gig descriptions)
    - Structured SQL queries (market stats, skill analytics)
        |
        v
  Claude API  -->  Web Chat UI (Flask)
```

## Key Features

**Scraping**
- Stealth browser automation with `nodriver` (undetected Chrome)
- Handles two different Fiverr page layouts (Data category vs Programming & Tech)
- Extracts: title, description, price range, seller level/country/languages, tags, ratings, delivery time
- Bulk scraping with configurable search queries and auto-pagination

**Data Pipeline**
- Normalized skills table with noise filtering and canonicalization
  - Filters out Fiverr UI junk, country names, task descriptions, job titles, generic terms
  - Merges duplicates (e.g., "Nodejs" + "Node.js", "reactjs" + "React")
- Pre-computed SQL views for instant market intelligence queries
- FTS5 full-text search for keyword retrieval

**RAG Chatbot**
- ChromaDB vector database with local embeddings (all-MiniLM-L6-v2) for semantic search
- Combines structured SQL analytics with semantic gig search for context
- Claude API for natural language responses with cited market data
- Web chat interface with markdown rendering (tables, headers, lists)
- CLI chat interface also available

## Setup

```bash
# Clone and install
git clone <repo-url>
cd Freelance-job-market-scraper
pip install -r requirements.txt

# Add your Anthropic API key
echo ANTHROPIC_API_KEY=sk-ant-your-key-here > .env
```

## Usage

### Web Chat UI
```bash
python web.py
# Open http://localhost:5000
```

### CLI Chat
```bash
python -u chatbot.py
```

### Scrape More Data
```bash
# Bulk scrape (runs until 1000+ gigs collected)
python -u scrape_bulk.py

# Single query scrape
python scrape_jobs.py --platform fiverr --query "python developer" --pages 5
```

### Live Dashboard (scraping progress)
```bash
python dashboard.py
# Open http://localhost:8050
```

## Project Structure

```
.
├── web.py              # Web chat UI (Flask)
├── chatbot.py          # CLI chatbot
├── rag.py              # RAG engine (ChromaDB + SQL)
├── db.py               # Database layer (SQLite, skills normalization, views)
├── config.py           # Configuration
├── scrape_bulk.py      # Bulk scraper (multi-query, targets 1000+ gigs)
├── scrape_jobs.py      # Single-query scraper
├── scrape_fix.py       # Re-scrape gigs with missing data
├── dashboard.py        # Live scraping progress dashboard
├── export.py           # Export data to Excel/CSV
├── scrapers/
│   ├── base.py         # Base scraper (browser automation, anti-detection)
│   └── fiverr.py       # Fiverr parser (JSON-LD, HTML, tags)
├── static/
│   └── index.html      # Chat UI frontend
└── data/
    ├── jobs.db          # SQLite database (gitignored)
    └── chroma/          # Vector embeddings (gitignored)
```

## Tech Stack

- **Scraping**: nodriver (undetected Chrome automation)
- **Database**: SQLite with FTS5 full-text search
- **Vector Search**: ChromaDB with all-MiniLM-L6-v2 embeddings
- **AI**: Claude API (Anthropic)
- **Web**: Flask + vanilla JS
- **Data Processing**: Python (regex parsing, JSON-LD extraction, noise filtering)

## Sample Output

The `beginner_opportunities` view surfaces skills where new sellers are actively getting work:

| Skill | Gigs | Avg Price | New Sellers | Entry Price |
|-------|------|-----------|-------------|-------------|
| Prompt engineering | 3 | $1,083 | 67% | $50 |
| Node.js | 20 | $299 | 40% | $5 |
| TensorFlow | 17 | $296 | 35% | $50 |
| PyTorch | 50 | $207 | 44% | $50 |
| React | 17 | $201 | 29% | $10 |
| Python | 228 | $107 | 30% | $5 |
| SQL | 143 | $92 | 32% | $5 |
