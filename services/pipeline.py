"""The scan: collect signals -> clean -> LLM extracts technologies -> merge and rank -> LLM proposes rings."""
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from config import BATCH_SIZE, DATA_DIR, PACKAGE_MAP, QUADRANTS, REGISTRY_SOURCE, REGISTRY_WORKERS, RINGS, RSS_FEEDS
from services import (ai_service, github_service, hn_service, radar_record, registry_service, rss_service, yc_service)

NOISE = re.compile(r"\b(raises|funding|series [a-d]|acquires|acquisition|layoffs?|hiring|podcast|webinar|"
                   r"episode|newsletter|sponsored|discount)\b", re.IGNORECASE)

# Extend these two during the day.
ALIASES = {
    "k8s": "kubernetes", "rag": "retrieval augmented generation",
    "retrieval-augmented generation": "retrieval augmented generation", "gh actions": "github actions",
    "golang": "go", "js": "javascript", "ts": "typescript", "mcp": "model context protocol",
}
GENERIC = {"ai", "artificial intelligence", "machine learning", "ml", "llm", "llms", "large language models",
           "generative ai", "genai", "cloud", "cloud computing", "security", "cybersecurity", "open source",
           "software", "api", "apis", "data", "devops", "agent", "agents", "ai agents", "automation", "web", "app", "apps"}


SOURCE_ORDER = ["GitHub", "Y Combinator", "Hacker News", "RSS feeds"]


def tech_key(name):
    k = str(name).lower()
    k = re.sub(r"\s+v?\d+(\.\d+)*$", "", k)
    k = re.sub(r"[^a-z0-9+#.\- ]+", " ", k)
    k = re.sub(r"\s+", " ", k).strip()
    return ALIASES.get(k, k)


def collect_signals(settings, progress=lambda msg, frac: None):
    """Fetch every enabled source. A failing source is reported, not fatal."""
    area, days, errors, signals = settings["area"], settings["days"], [], []
    sources = settings["sources"]
    # No fixed cap: each source fetches enough for the number of signals asked for (never less than before: 15, 10, 15 and 4 per feed).
    share = math.ceil(settings["max_signals"] / max(1, sum(1 for s in SOURCE_ORDER if s in sources)))
    steps = [
        ("GitHub", lambda: github_service.get_trending_repositories(limit=max(15, share), technology_area=area, days=days)),
        ("Y Combinator", lambda: yc_service.get_yc_companies(limit=max(10, share), technology_area=area)),
        ("Hacker News", lambda: hn_service.get_hn_stories(limit=max(15, share), technology_area=area, days=days)),
        ("RSS feeds", lambda: rss_service.get_rss_items(RSS_FEEDS, per_feed=max(4, math.ceil(share / max(1, len(RSS_FEEDS)))),
                                                        technology_area=area, days=days, errors=errors)),
    ]
    enabled = [s for s in steps if s[0] in sources]
    for i, (name, fetch) in enumerate(enabled):
        progress(f"Collecting from {name}...", i / max(len(enabled), 1))
        cached = _cache_get(name, settings, share=share) if settings.get("use_cache", True) else None
        if cached is not None:
            signals.extend(cached)
            continue
        try:
            items = fetch()
            signals.extend(items)
            _cache_put(name, settings, items, share=share)
        except Exception as error:
            errors.append(f"{name}: {error}")
            stale = _cache_get(name, settings, max_age=7 * 86400, share=share)   # better old data than none
            if stale:
                signals.extend(stale)
                errors.append(f"{name}: used cached data from an earlier fetch")
    return signals, errors


CACHE_DIR = os.path.join(os.path.dirname(DATA_DIR), "cache")
CACHE_TTL = 3600  # seconds: re-running a scan within an hour reuses the fetched sources (saves the GitHub rate limit)


def _cache_file(name, settings, share=None):
    key = re.sub(r"[^a-z0-9]+", "-", f"{name}-{settings['area']}-{settings['days']}{'' if share is None else f'-{share}'}".lower())
    return os.path.join(CACHE_DIR, key + ".json")


def _cache_get(name, settings, max_age=CACHE_TTL, share=None):
    path = _cache_file(name, settings, share)
    if not os.path.exists(path) or time.time() - os.path.getmtime(path) > max_age:
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cache_put(name, settings, items, share=None):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_file(name, settings, share), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)


def select_signals(signals, max_signals):
    """Drop noise and duplicates, then pick round robin across sources so one source can't dominate."""
    seen, unique = set(), []
    for s in signals:
        if NOISE.search(s["title"]):
            continue
        k1 = re.sub(r"[?#].*$", "", s["url"]).rstrip("/").lower()
        k2 = s["title"].lower()
        if k1 in seen or k2 in seen:
            continue
        seen.update([k1, k2])
        unique.append(s)

    groups = {}
    for s in unique:
        groups.setdefault(s["group"], []).append(s)
    for g in groups.values():
        g.sort(key=lambda s: s.get("date") or "", reverse=True)
    queues, picked = list(groups.values()), []
    while len(picked) < max_signals and any(queues):
        for q in queues:
            if q and len(picked) < max_signals:
                picked.append(q.pop(0))
    for i, s in enumerate(picked, start=1):
        s["n"] = i
    return picked


EVIDENCE_PER_SOURCE = 3  # evidence lines per source in the rating prompt, so one busy source cannot crowd out the others


def trim_evidence(evidence):
    """Keep the first few items of every source, with `meta` (stars, points, ...) which the rules and the registry lookup need."""
    kept, count = [], {}
    for e in evidence:
        if count.get(e["source"], 0) < EVIDENCE_PER_SOURCE:
            count[e["source"]] = count.get(e["source"], 0) + 1
            kept.append({k: e.get(k) for k in ("source", "title", "url", "text", "date", "meta")})
    return kept


def merge_candidates(signals, extractions, top_n=None):
    """extractions: list of (batch_signals, technologies). Same technology across batches is merged.
    top_n: keep the most mentioned this many; None keeps every technology found."""
    by_key = {}
    for batch, techs in extractions:
        for t in techs or []:
            key = tech_key(t.get("name", ""))
            if len(key) < 2 or key in GENERIC:
                continue
            c = by_key.setdefault(key, {"key": key, "name": t["name"], "what": t.get("what", ""),
                                        "votes": {}, "evidence": [], "sources": []})
            q = t.get("quadrant")
            if q in QUADRANTS:
                c["votes"][q] = c["votes"].get(q, 0) + 1
            for n in t.get("items", []) or []:
                s = next((x for x in batch if x["n"] == n), None)
                if s and not any(e["url"] == s["url"] for e in c["evidence"]):
                    c["evidence"].append(s)
                    if s["source"] not in c["sources"]:
                        c["sources"].append(s["source"])

    candidates = []
    today = date.today()
    for c in by_key.values():
        if not c["evidence"]:
            continue  # no linked source = not on the radar
        repo_ages, own_repos = [], []   # own = a GitHub repository that IS the technology, not one that mentions it
        for e in c["evidence"]:
            created = (e.get("meta") or {}).get("created_at")
            if e["source"] == "GitHub" and created and tech_key((e.get("meta") or {}).get("name", "")) == c["key"]:
                repo_ages.append((today - datetime.strptime(created, "%Y-%m-%d").date()).days)
                own_repos.append(e["url"])
        candidates.append({
            "name": c["name"], "key": c["key"], "what": c["what"],
            "quadrant": max(c["votes"], key=c["votes"].get) if c["votes"] else "Tools",
            "mentions": len(c["evidence"]), "sources": c["sources"],
            "signal": len(c["evidence"]) + 2 * len(c["sources"]),
            "youngest_repo_days": min(repo_ages) if repo_ages else None,
            "own_repos": own_repos,
            "evidence": trim_evidence(c["evidence"]),
        })
    candidates.sort(key=lambda c: c["signal"], reverse=True)
    return candidates[:top_n]


def attach_registry_evidence(candidates, settings, progress=lambda msg, frac: None):
    """The fifth source: look every technology up in the package registries (npm, PyPI, Maven Central, Docker Hub) and add what
    was found to its evidence, where the rating prompt reads it. `mentions` and `sources` stay as they are: a download count
    is not a mention. Returns the warnings."""

    def lookup(c):
        name = f"packages {c['key']}"
        cached = _cache_get(name, settings) if settings.get("use_cache", True) else None
        if cached is not None:
            return cached, []
        errors = []
        try:
            records = registry_service.lookup_registries(c["name"], c["key"], c.get("own_repos"), PACKAGE_MAP.get(c["key"]),
                                                         errors)
        except Exception as error:   # a surprise in one lookup must not stop the scan
            return [], [f"{c['name']}: {error}"]
        if not errors:   # a partial answer would otherwise be reused for an hour
            _cache_put(name, settings, records)
        return records, errors

    warnings = []
    with ThreadPoolExecutor(max_workers=REGISTRY_WORKERS) as pool:   # many small requests: several technologies at once
        for i, (c, (records, errors)) in enumerate(zip(candidates, pool.map(lookup, candidates)), start=1):
            progress(f"Package registries: looked up {c['name']} ({i}/{len(candidates)})...", i / len(candidates))
            c["evidence"] += records
            warnings += errors
    return list(dict.fromkeys(f"Package registries: {w}" for w in warnings))   # one line per distinct problem


def apply_rules(candidate, answer):
    """Checks in code what the prompt asks, so a small model cannot break the rules. Only facts are rules (radar_record.apply_guards:
    an archived repository, a license nobody can use, a technology too new to be proven, ADOPT without years of use); what a model
    only guessed from headlines never overrules the model's ring."""
    ring = answer.get("ring") if answer.get("ring") in RINGS else "Assess"
    ring, notes = radar_record.apply_guards(ring, radar_record.build_record(candidate))
    quadrant = answer.get("quadrant") if answer.get("quadrant") in QUADRANTS else candidate["quadrant"]
    return ring, quadrant, notes


def run_scan(settings, llm, progress=lambda msg, frac: None):
    """llm: the Ollama wrapper: it extracts the technologies and rates each one with the single prompt."""
    started = datetime.now()
    settings = dict(settings, prompts=ai_service.prompt_versions())
    raw, errors = collect_signals(settings, lambda m, f: progress(m, 0.15 * f))
    signals = select_signals(raw, settings["max_signals"])
    if not signals:
        return {"run_at": started.isoformat(timespec="seconds"), "settings": settings, "signals": [],
                "technologies": [], "errors": errors + ["No signals collected."], "stats": {"raw": len(raw)}}

    # Batches never mix sources, so each batch gets the prompt written for its source.
    batches = []
    for source in [s for s in SOURCE_ORDER if any(x["source"] == s for x in signals)]:
        items = [x for x in signals if x["source"] == source]
        batches += [(source, items[i:i + BATCH_SIZE]) for i in range(0, len(items), BATCH_SIZE)]
    extractions = []
    for i, (source, batch) in enumerate(batches):
        progress(f"LLM: finding technologies in {source} batch ({i + 1}/{len(batches)})...", 0.15 + 0.45 * i / len(batches))
        try:
            extractions.append((batch, ai_service.extract_technologies(llm, batch, source)))
        except Exception as error:
            errors.append(f"Extraction {source} batch {i + 1}: {error}")

    candidates = merge_candidates(signals, extractions, settings.get("top_n"))
    if REGISTRY_SOURCE in settings["sources"]:   # the fifth source needs the technologies, so it runs after the merge
        errors += attach_registry_evidence(candidates, settings, lambda m, f: progress(m, 0.6 + 0.05 * f))

    # Rating: ONE prompt per technology (prompts/classify.md) reads all its evidence, then the guards in code (apply_rules).
    technologies = []
    for i, c in enumerate(candidates):
        progress(f"LLM: rating {c['name']} ({i + 1}/{len(candidates)})...", 0.65 + 0.35 * i / max(len(candidates), 1))
        try:
            answer = ai_service.classify_technology(llm, c)
        except Exception as error:
            errors.append(f"Classify {c['name']}: {error}")
            continue
        ring, quadrant, notes = apply_rules(c, answer)
        technologies.append({
            "name": c["name"], "ring": ring, "quadrant": quadrant, "llm_ring": answer.get("ring"),
            "relevance": answer.get("relevance", "LOW"), "confidence": answer.get("confidence", "low"),
            "summary": answer.get("summary", ""), "reason": answer.get("reason", ""),
            "business_value": answer.get("business_value", ""), "rule_notes": notes,
            "mentions": c["mentions"], "sources": c["sources"], "evidence": c["evidence"],
            "standing": radar_record.build_record(c)["standing"],
        })

    progress("Done", 1.0)
    return {
        "run_at": started.isoformat(timespec="seconds"),
        "duration_s": round((datetime.now() - started).total_seconds()),
        "settings": settings,
        "signals": signals,
        "technologies": technologies,
        "errors": errors,
        "stats": {"raw": len(raw), "signals": len(signals), "candidates": len(candidates)},
    }
