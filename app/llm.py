"""Optional local language model for the chat screen.

The model only ever sees passages that keyword search already retrieved for this user (so access
control still holds), and is told to answer from them alone and cite them. With no model
configured, chat still works: it returns the passages themselves.

    LLM_API=ollama   LLM_URL=http://localhost:11434   LLM_MODEL=qwen2.5:7b-instruct
    LLM_API=openai   LLM_URL=http://localhost:8080    LLM_MODEL=local   (llama.cpp server, vLLM, LM Studio)
"""
from __future__ import annotations

import os

import httpx

SYSTEM_PROMPT = """You are the knowledge bank assistant for a financial advisory firm.
Answer the user's question using ONLY the numbered sources provided with it.
- Cite the source of every statement with its number in square brackets, e.g. [1] or [2][3].
- Quote figures, percentages, dates and policy names exactly as they appear in the sources.
- If the sources do not contain the answer, say so plainly and suggest what the user could search
  for or upload instead. Never fill gaps from general knowledge.
- Be concise. Use short paragraphs or bullet points.
- You provide information from documents, not financial advice."""

MAX_HISTORY_TURNS = 6


def configured() -> bool:
    return bool(os.environ.get("LLM_URL") and os.environ.get("LLM_MODEL"))


def model_name() -> str | None:
    return os.environ.get("LLM_MODEL") if configured() else None


def format_sources(passages: list[dict]) -> str:
    blocks = []
    for n, p in enumerate(passages, start=1):
        where = f"sheet {p['label']}" if p.get("label") else f"page {p['page_no']}"
        meta = ", ".join(str(v) for v in (p.get("issuer"), p.get("document_type"),
                                           p.get("effective_date") and f"effective {p['effective_date']}") if v)
        blocks.append(f"[{n}] {p['title']} ({where}{'; ' + meta if meta else ''})\n{p['passage']}")
    return "\n\n".join(blocks)


def build_messages(question: str, history: list[dict], passages: list[dict]) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[-MAX_HISTORY_TURNS:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    sources = format_sources(passages) if passages else "(no matching passages were found)"
    messages.append({"role": "user", "content": f"Sources:\n\n{sources}\n\nQuestion: {question}"})
    return messages


def complete(messages: list[dict]) -> str:
    """One non-streaming completion. Temperature 0 so the same question gets the same answer."""
    api = os.environ.get("LLM_API", "ollama").lower()
    url = os.environ["LLM_URL"].rstrip("/")
    model = os.environ["LLM_MODEL"]
    timeout = float(os.environ.get("LLM_TIMEOUT", "120"))
    if api == "ollama":
        r = httpx.post(f"{url}/api/chat", timeout=timeout, json={
            "model": model, "messages": messages, "stream": False,
            "options": {"temperature": 0, "seed": 7, "num_ctx": 8192},
        })
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    if api == "openai":
        r = httpx.post(f"{url}/v1/chat/completions", timeout=timeout,
                       headers={"Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'none')}"},
                       json={"model": model, "messages": messages, "temperature": 0, "seed": 7})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    raise ValueError(f"Unknown LLM_API: {api}")


def answer(question: str, history: list[dict], passages: list[dict]) -> tuple[str, str]:
    """Returns (answer text, mode). mode is 'llm', 'passages' (no model configured) or 'llm_error'."""
    if not passages:
        return ("I couldn't find anything about that in the documents you can see. "
                "Try different words, check the filters, or upload the document."), "no_results"
    if not configured():
        return "Here's what I found in your documents:", "passages"
    try:
        return complete(build_messages(question, history, passages)), "llm"
    except Exception as e:
        return (f"The language model didn't respond ({type(e).__name__}), so here are the most relevant "
                "passages instead."), "llm_error"
