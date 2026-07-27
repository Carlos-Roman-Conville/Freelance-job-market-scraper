"""Freelance Career Intelligence Chatbot — powered by RAG + Claude."""
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, SYSTEM_PROMPT
from rag import RAGEngine


def main():
    if not ANTHROPIC_API_KEY:
        print("Set ANTHROPIC_API_KEY in your .env file:")
        print("  echo ANTHROPIC_API_KEY=sk-ant-... >> .env")
        sys.exit(1)

    print("Loading RAG engine...")
    rag = RAGEngine()
    client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=90.0)

    print("\n" + "=" * 60)
    print("  Freelance Career Intelligence Agent")
    print("  Ask about skills, niches, and opportunities.")
    print("  Type 'quit' to exit.")
    print("=" * 60 + "\n")

    history = []

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            break

        try:
            context = rag.build_context(question)
        except Exception as e:
            print(f"\nRAG error: {e}\n")
            continue

        messages = list(history)
        messages.append({
            "role": "user",
            "content": f"[MARKET DATA]\n{context}\n\n[QUESTION]\n{question}",
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
        except Exception as e:
            print(f"\nAPI error: {e}\n")
            continue

        if not answer:
            print("\nAgent: (no response)\n")
            continue
        print(f"\nAgent: {answer}\n")

        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        if len(history) > 20:
            history = history[-20:]

    rag.close()
    print("Goodbye!")


if __name__ == "__main__":
    main()
