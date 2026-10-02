"""Talks to the local LLM (Ollama). Two steps:
1. extract_technologies: which technologies do these signals talk about?
2. classify_technology: which ring should one technology get, and why?
Both force the answer into a JSON schema, so the model cannot invent other ring names."""
import json

import ollama

from config import BBV_CONTEXT, QUADRANTS, RINGS

EXTRACT_SYSTEM = """You extract technologies from tech news for a technology radar.
A technology is a named tool, platform, framework, language, library, standard or engineering technique, for example "Kubernetes", "Rust", "OpenTelemetry", "Retrieval-augmented generation" or "Trunk-based development".
NOT technologies: companies, startups, people, events, consumer products, funding or business news, and generic words such as "AI", "cloud", "security" or "open source".
A GitHub repository counts as a technology when it is itself a tool, library or framework; use its project name.
For a startup, list only the technologies it is built on or builds for, if the text names them; otherwise skip it.
Rules:
- Only list a technology if it is a main subject of the item.
- Use the most common official name, without version numbers.
- Pick one quadrant: Techniques, Tools, Platforms, or Languages & Frameworks.
- "what" is one short sentence saying what it is.
- "items" lists the numbers of the news items that mention it.
- Items without a technology are skipped. An empty list is a valid answer.
Answer only with JSON that matches the schema."""

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

CLASSIFY_SYSTEM = f"""You help bbv's engineers maintain a technology radar.
About bbv: {BBV_CONTEXT}

Rings:
- Adopt: mature, widely used in production, low risk. Our default choice.
- Trial: ready to use on a real project that can handle some risk.
- Assess: promising. Worth a spike or proof of concept to understand the impact.
- Hold: hyped, immature, risky or replaced by something better. Proceed with caution.

Rules:
- Judge only from the evidence given. Do not invent facts, numbers or users.
- You have no information about bbv's own experience, so propose Adopt only if the evidence clearly shows broad, mature production use.
- A project created in the last few months cannot be Adopt or Trial: at most Assess.
- If the evidence is thin, choose Assess with low confidence.
- "summary": one sentence on what it is.
- "reason": at most 2 sentences, based on the evidence.
- "business_value": 1 sentence on which bbv customers or services it could matter for.
- "relevance": how relevant it is for bbv: HIGH, MEDIUM or LOW.
Answer only with JSON that matches the schema."""

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
        if any(x in self.model for x in ("qwen3", "deepseek-r1", "gpt-oss")):
            kwargs["think"] = False  # thinking is very slow on CPU
        response = self.client.chat(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format=schema,
            options={"temperature": 0, "num_ctx": 8192},
            **kwargs,
        )
        message = response["message"] if isinstance(response, dict) else response.message
        content = message["content"] if isinstance(message, dict) else message.content
        return json.loads(content)


def extract_technologies(llm, signals):
    """signals: list of dicts with a running number 'n'. Returns the model's technologies list."""
    user = "News items:\n" + "\n".join(
        f"[{s['n']}] {s['title']} - {s['text']} (source: {s['source']})" for s in signals
    )
    result = llm.chat_json(EXTRACT_SYSTEM, user, EXTRACT_SCHEMA)
    return result.get("technologies", [])


def classify_technology(llm, candidate):
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
    lines += [f"- {e.get('date') or 'n/a'} {e['source']}: {e['title']} - {e['text']}" for e in candidate["evidence"]]
    return llm.chat_json(CLASSIFY_SYSTEM, "\n".join(lines), CLASSIFY_SCHEMA)


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
