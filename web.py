"""Web chat interface for the Freelance Career Intelligence Agent."""
import atexit
import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from flask import Flask, request, jsonify, send_from_directory
from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, SYSTEM_PROMPT_WEB as SYSTEM_PROMPT
from rag import RAGEngine

app = Flask(__name__, static_folder="static")
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

if not ANTHROPIC_API_KEY:
    print("Set ANTHROPIC_API_KEY in your .env file")
    sys.exit(1)

print("Loading RAG engine...")
rag = RAGEngine()
atexit.register(rag.close)
client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=90.0)
print("RAG engine ready.")

MAX_SESSIONS = 200
SESSION_TTL = 3600

conversations = {}
conversation_times = {}
conv_lock = threading.Lock()


def _evict_sessions():
    now = time.time()
    expired = [sid for sid, ts in conversation_times.items() if now - ts > SESSION_TTL]
    for sid in expired:
        conversations.pop(sid, None)
        conversation_times.pop(sid, None)
    if len(conversations) > MAX_SESSIONS:
        oldest = sorted(conversation_times, key=conversation_times.get)
        for sid in oldest[:len(conversations) - MAX_SESSIONS]:
            conversations.pop(sid, None)
            conversation_times.pop(sid, None)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400
    raw_message = data.get("message")
    message = str(raw_message).strip() if raw_message else ""
    session_id = str(data.get("session_id") or "default")

    if not message:
        return jsonify({"error": "Empty message"}), 400

    with conv_lock:
        _evict_sessions()
        if session_id not in conversations:
            conversations[session_id] = []
        history = list(conversations[session_id])
        conversation_times[session_id] = time.time()

    try:
        context = rag.build_context(message)
    except Exception as e:
        logging.exception("RAG context error for session %s", session_id)
        return jsonify({"error": "Failed to build context. Please try again."}), 500

    messages = list(history)
    messages.append({
        "role": "user",
        "content": f"[MARKET DATA]\n{context}\n\n[QUESTION]\n{message}",
    })

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        answer = ""
        for block in response.content:
            if hasattr(block, "text"):
                answer = block.text
                break
        if not answer:
            return jsonify({"error": "No text in response"}), 500
    except Exception as e:
        logging.exception("Chat API error for session %s", session_id)
        return jsonify({"error": "Failed to generate response. Please try again."}), 500

    with conv_lock:
        if session_id not in conversations:
            conversations[session_id] = []
        conversations[session_id].append({"role": "user", "content": message})
        conversations[session_id].append({"role": "assistant", "content": answer})
        if len(conversations[session_id]) > 20:
            conversations[session_id] = conversations[session_id][-20:]
        conversation_times[session_id] = time.time()

    return jsonify({"response": answer})


@app.route("/api/stats", methods=["GET"])
def stats():
    try:
        cur = rag.db.cursor()
        cur.execute("SELECT COUNT(*) FROM fiverr_gigs")
        total_gigs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM skills")
        total_skills = cur.fetchone()[0]
        cur.execute("SELECT AVG(price_min) FROM fiverr_gigs")
        avg_price = cur.fetchone()[0] or 0
        cur.execute(
            "SELECT skill, gig_count, avg_price FROM beginner_opportunities ORDER BY avg_price DESC LIMIT 5"
        )
        top = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM upwork_jobs")
        upwork_total = cur.fetchone()[0]
        cur.execute(
            "SELECT AVG(budget_min) FROM upwork_jobs WHERE budget_type='Hourly' AND budget_min > 0"
        )
        upwork_avg_hourly = cur.fetchone()[0] or 0
        return jsonify({
            "total_gigs": total_gigs,
            "total_skills": total_skills,
            "avg_price": round(float(avg_price)),
            "top_skills": [{"skill": r[0], "gigs": r[1], "avg_price": round(float(r[2] or 0))} for r in top],
            "upwork_total": upwork_total,
            "upwork_avg_hourly": round(float(upwork_avg_hourly)),
        })
    except Exception:
        logging.exception("Stats endpoint error")
        try:
            rag.db.rollback()
        except Exception:
            pass
        return jsonify({"error": "Failed to load stats"}), 500


if __name__ == "__main__":
    print("Starting web server on http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
