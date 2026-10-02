"""Talks to the local LLM (Ollama). Two steps:
1. extract_technologies: which technologies do these signals talk about?
2. classify_technology: which ring should one technology get, and why?
Both force the answer into a JSON schema, so the model cannot invent other ring names."""
import hashlib
import json
import os

import ollama

from config import BBV_CONTEXT, OLLAMA_NUM_THREAD, QUADRANTS, RINGS

PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

# One extraction prompt per source (prompts/<file>.md) + shared rules (prompts/_rules.md).
SOURCE_PROMPTS = {"GitHub": "github", "Y Combinator": "ycombinator", "Hacker News": "hackernews", "RSS feeds": "rss"}

# Rating prompts of the multi-agent graph (services/radar_graph.py): one agent per source, a skeptic and a judge.
RATING_PROMPTS = ["rate_github", "rate_ycombinator", "rate_hackernews", "rate_rss", "rate_packages", "antihype", "judge"]


def prompt_path(name):
    return os.path.join(PROMPT_DIR, f"{name}.md")


def load_prompt(name):
    """Read on every call, so edits in the Prompts tab (or in the file) apply to the next scan."""
    with open(prompt_path(name), encoding="utf-8") as f:
        return f.read().strip()


def save_prompt(name, text):
    with open(prompt_path(name), "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")


def prompt_versions():
    """Short fingerprint of every prompt, stored with each run so runs can be compared."""
    names = list(SOURCE_PROMPTS.values()) + ["_rules", "classify"] + RATING_PROMPTS
    return {n: hashlib.md5(load_prompt(n).encode()).hexdigest()[:8] for n in names}


def extraction_system(source):
    return load_prompt(SOURCE_PROMPTS.get(source, "rss")) + "\n\n" + load_prompt("_rules")


def classify_system():
    return load_prompt("classify").replace("{bbv_context}", BBV_CONTEXT)


def _meta(s, key, default=""):
    return (s.get("meta") or {}).get(key, default) or default


# How each source's items are shown to the model: only the fields that matter for that source.
def format_item(s):
    source = s["source"]
    if source == "GitHub":
        topics = ", ".join(_meta(s, "topics", [])[:6]) or "none"
        return (f"[{s['n']}] repo {_meta(s, 'full_name', s['title'])} | language: {_meta(s, 'language', 'unknown')} | "
                f"{_meta(s, 'stars', '?')} stars | created {_meta(s, 'created_at', '?')} | topics: {topics} | "
                f"description: {_meta(s, 'description', s['text'])}")
    if source == "Y Combinator":
        return (f"[{s['n']}] startup {_meta(s, 'company', s['title'])} (batch {_meta(s, 'batch', '?')}) | "
                f"tags: {', '.join(_meta(s, 'tags', [])) or 'none'} | {_meta(s, 'one_liner', s['text'])}")
    if source == "Hacker News":
        return (f"[{s['n']}] story \"{s['title']}\" | {_meta(s, 'points', 0)} points, {_meta(s, 'comments', 0)} comments | "
                f"link: {_meta(s, 'domain', 'news.ycombinator.com')}")
    return f"[{s['n']}] article on {s.get('group', 'a blog')}: \"{s['title']}\" | {s['text']}"


EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "technologies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quadrant": {"type": "string", "enum": QUADRANTS},
                    "what": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "quadrant", "what", "items"],
            },
        }
    },
    "required": ["technologies"],
}

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "quadrant": {"type": "string", "enum": QUADRANTS},
        "ring": {"type": "string", "enum": RINGS},
        "summary": {"type": "string"},
        "reason": {"type": "string"},
        "business_value": {"type": "string"},
        "relevance": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["quadrant", "ring", "summary", "reason", "business_value", "relevance", "confidence"],
}


_THINKING = {}


def thinking_setting(host, model):
    """What to send as `think` so a thinking model answers at once: False, "low" for gpt-oss (it cannot switch thinking off),
    None for a model that does not think. Ollama knows which models think (qwen3, gemma4, nemotron, lfm2.5-thinking, ...), so
    ask it; the three names below are only the fallback when it cannot be asked."""
    if (host, model) not in _THINKING:
        try:
            thinks = "thinking" in (ollama.Client(host=host, timeout=10).show(model).capabilities or [])
        except Exception:
            thinks = any(x in model for x in ("qwen3", "deepseek-r1", "gpt-oss"))
        _THINKING[(host, model)] = None if not thinks else "low" if "gpt-oss" in model else False
    return _THINKING[(host, model)]


class LLM:
    def __init__(self, host, model, timeout=600):
        self.host = host
        self.model = model
        self.client = ollama.Client(host=host, timeout=timeout)

    def available_models(self):
        response = self.client.list()
        models = getattr(response, "models", None)
        if models is None and isinstance(response, dict):
            models = response.get("models", [])
        names = []
        for m in models or []:
            name = getattr(m, "model", None) or (m.get("model") or m.get("name") if isinstance(m, dict) else None)
            if name:
                names.append(name)
        return names

    def chat_json(self, system, user, schema):
        kwargs = {}
        think = thinking_setting(self.host, self.model)
        if think is not None:
            kwargs["think"] = think  # thinking is very slow on CPU
        options = {"temperature": 0, "num_ctx": 8192}
        if OLLAMA_NUM_THREAD:
            options["num_thread"] = OLLAMA_NUM_THREAD  # see config.py
        response = self.client.chat(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format=schema,
            options=options,
            **kwargs,
        )
        message = response["message"] if isinstance(response, dict) else response.message
        content = message["content"] if isinstance(message, dict) else message.content
        return json.loads(content)


def extract_technologies(llm, signals, source=None):
    """signals: items of ONE source, each with a running number 'n'. Uses that source's prompt."""
    source = source or signals[0]["source"]
    user = f"Items from {source}:\n" + "\n".join(format_item(s) for s in signals)
    result = llm.chat_json(extraction_system(source), user, EXTRACT_SCHEMA)
    return result.get("technologies", [])


def evidence_lines(candidate):
    """Every evidence item of a candidate as one dated line. The single prompt and the judge of the agents read the same lines."""
    return [f"- {e.get('date') or 'n/a'} {e['source']}: {e['title']} - {e['text']}" for e in candidate["evidence"]]


def classify_message(candidate):
    """What the single prompt is shown about one technology."""
    lines = [
        f"Technology: {candidate['name']}",
        f"What it is: {candidate.get('what', '')}",
        f"Suggested quadrant: {candidate.get('quadrant', '')}",
        f"Mentioned in {candidate['mentions']} items from {len(candidate['sources'])} different sources "
        f"({', '.join(candidate['sources'])}).",
    ]
    if candidate.get("youngest_repo_days") is not None:
        lines.append(f"Its GitHub repository was created {candidate['youngest_repo_days']} days ago.")
    lines.append("Evidence:")
    lines += evidence_lines(candidate)
    return "\n".join(lines)


def classify_technology(llm, candidate):
    return llm.chat_json(classify_system(), classify_message(candidate), CLASSIFY_SCHEMA)


def analyze_technology(tech, host="http://localhost:11434", model="llama3.2:3b"):
    """Kept for the original test_ai.py / test_yc_ai.py: classify one item directly."""
    description = tech.get("description") or tech.get("text", "")
    candidate = {
        "name": tech.get("full_name") or tech.get("name") or tech.get("title", ""),
        "what": description,
        "quadrant": "",
        "mentions": 1,
        "sources": [tech.get("source", "Unknown")],
        "evidence": [{"source": tech.get("source", "Unknown"),
                      "title": tech.get("full_name") or tech.get("name") or tech.get("title", ""),
                      "text": f"{description} | stars: {tech.get('stars')} | topics: {tech.get('topics')}",
                      "date": ""}],
    }
    result = classify_technology(LLM(host, model), candidate)
    result["category"] = result.get("ring", "Assess").upper()
    return result
