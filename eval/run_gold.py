"""Test a model against technologies whose ring is not a matter of opinion (eval/gold_radar.json).

For every technology it collects REAL evidence by name (its GitHub repository, Hacker News stories, engineering press articles,
package registries) and saves it in data/eval/evidence, so every model is shown exactly the same facts. Then it rates the
technology the way the app does (the source agents, anti-hype and judge, then the simple rules) and compares the ring with
the gold one. Run it again with another model and compare.

    python eval/run_gold.py --model qwen3:4b --host http://localhost:11435     all technologies, the full agents
    python eval/run_gold.py --limit 2                                           2 per ring: a quick try
    python eval/run_gold.py --mode classic                                      the old single prompt: what the model knows
    python eval/run_gold.py --evidence-only                                     only collect and show what each lane has
    python eval/run_gold.py --verify                                            re-check that the gold labels are still true
    python eval/run_gold.py --compare                                           side by side: every saved result
"""
import argparse
import asyncio
import glob
import json
import os
import re
import sys
import time
from datetime import date, datetime
from urllib.parse import urlparse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from services import ai_service, github_service, pipeline, radar_graph, registry_service, rss_service  # noqa: E402

GOLD = os.path.join(ROOT, "eval", "gold_radar.json")
EVAL_DIR = os.path.join(ROOT, "data", "eval")   # data/ is git-ignored: evidence and results stay on this machine
PRESS_SITES = ["https://thenewstack.io", "https://github.blog", "https://devblogs.microsoft.com/dotnet", "https://www.cncf.io"]
RINGS = config.RINGS


# ------------------------------------------------------------------ the gold list
def load_gold(path=GOLD, rings=None, names=None, limit=None):
    """The technologies to test. `limit` is per ring, so even a quick try covers every ring."""
    items = json.load(open(path, encoding="utf-8"))["items"]
    if rings:
        items = [i for i in items if i["ring"] in rings]
    if names:
        items = [i for i in items if i["name"].lower() in names or i["key"] in names]
    if limit:
        seen, kept = {}, []
        for i in items:
            seen[i["ring"]] = seen.get(i["ring"], 0) + 1
            if seen[i["ring"]] <= limit:
                kept.append(i)
        items = kept
    return items


# ------------------------------------------------------------------ evidence, collected by name
def _get(url, params=None):
    headers = {"User-Agent": "bbv-tech-radar", "Accept": "application/json"}
    token = os.getenv("GITHUB_TOKEN", "").strip().strip('"')
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, params=params, headers=headers, timeout=25)
    response.raise_for_status()
    return response.json()


def _words(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def mentions(text, terms):
    """True when the text contains every word of at least one search term ("Python 2" needs both "python" and "2")."""
    have = _words(text)
    return any(_words(term) <= have for term in terms)


def hn_evidence(terms, min_points=100, keep=5):
    """The most popular Hacker News stories whose title is about the technology (any date)."""
    found = {}
    for term in terms:
        data = _get("https://hn.algolia.com/api/v1/search", {"query": term, "tags": "story", "numericFilters": f"points>{min_points}",
                                                             "hitsPerPage": 30, "restrictSearchableAttributes": "title"})
        for hit in data.get("hits", []):
            if hit.get("title") and mentions(hit["title"], terms):
                found[hit["objectID"]] = hit
    top = sorted(found.values(), key=lambda h: -h.get("points", 0))[:keep]
    return [{"source": "Hacker News", "group": "hackernews", "title": h["title"],
             "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
             "text": f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments on Hacker News",
             "date": (h.get("created_at") or "")[:10],
             "meta": {"points": h.get("points", 0), "comments": h.get("num_comments", 0),
                      "domain": urlparse(h.get("url") or "").netloc.replace("www.", "") or "news.ycombinator.com"}} for h in top]


def press_evidence(terms, errors, per_site=2, keep=6):
    """Articles about the technology from engineering sites (their WordPress search, any date)."""
    items, seen = [], set()
    for site in PRESS_SITES:
        got = 0
        for term in terms:
            if got >= per_site:
                break
            try:
                posts = _get(f"{site}/wp-json/wp/v2/posts", {"search": term, "per_page": 8, "_fields": "title,link,date,excerpt"})
            except Exception as error:
                errors.append(f"press {site}: {error}")
                break
            for p in posts:
                title, excerpt = rss_service._clean(p["title"]["rendered"]), rss_service._clean(p["excerpt"]["rendered"])
                if p["link"] in seen or not mentions(title, terms):   # the title must be about it, not just mention it
                    continue
                seen.add(p["link"])
                got += 1
                items.append({"source": "RSS feeds", "group": urlparse(p["link"]).netloc.replace("www.", ""), "title": title,
                              "url": p["link"], "text": excerpt[:300], "date": (p.get("date") or "")[:10], "meta": {"feed": site}})
                if got >= per_site:
                    break
    return items[:keep]


def collect_evidence(item, refresh=False, directory=None):
    """{"evidence", "own_repos", "errors"} for one gold item. Saved, and reused unless `refresh`: the same facts for every model."""
    directory = directory or os.path.join(EVAL_DIR, "evidence")
    path = os.path.join(directory, re.sub(r"[^a-z0-9]+", "-", item["key"]) + ".json")
    if not refresh and os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    errors, evidence, own = [], [], []
    terms = item.get("search") or [item["name"]]
    if item.get("repo"):
        try:
            signal = github_service.repo_signal(_get(f"https://api.github.com/repos/{item['repo']}"))
            evidence.append(signal)
            own = [signal["url"]]
        except Exception as error:
            errors.append(f"GitHub {item['repo']}: {error}")
    try:
        evidence += hn_evidence(terms)
    except Exception as error:
        errors.append(f"Hacker News: {error}")
    evidence += press_evidence(terms, errors)
    evidence = pipeline.trim_evidence(evidence)   # the same cap per source as a real scan
    if item.get("packages"):                      # curated only: a name alone is never evidence
        evidence += registry_service.lookup_registries(item["name"], item["key"], own, item["packages"], errors)
    data = {"evidence": evidence, "own_repos": own, "errors": errors, "collected_at": datetime.now().isoformat(timespec="seconds")}
    if not errors:   # an incomplete snapshot would make every later run unfair: collect again next time
        os.makedirs(directory, exist_ok=True)
        json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return data


def build_candidate(item, collected):
    """What pipeline.merge_candidates would hand to the agents. Registry data is evidence, not a mention."""
    evidence = collected["evidence"]
    signals = [e for e in evidence if e["source"] != config.REGISTRY_SOURCE]
    created = next((e["meta"].get("created_at") for e in signals if e["source"] == "GitHub"), None)
    age = (date.today() - datetime.strptime(created, "%Y-%m-%d").date()).days if created and collected["own_repos"] else None
    sources = list(dict.fromkeys(e["source"] for e in signals))
    return {"name": item["name"], "key": item["key"], "what": item["what"], "quadrant": item["quadrant"], "mentions": len(signals),
            "sources": sources, "signal": len(signals) + 2 * len(sources), "youngest_repo_days": age,
            "own_repos": collected["own_repos"], "evidence": evidence}


def lanes_with_data(candidate):
    names = {"GitHub": "github", "Hacker News": "hackernews", "RSS feeds": "rss", "Y Combinator": "ycombinator", config.REGISTRY_SOURCE: "packages"}
    return sorted({names[e["source"]] for e in candidate["evidence"]})


# ------------------------------------------------------------------ rating
def rate_agents(candidates, chat_model, parallel=2, on_done=lambda i, c: None):
    """The app's rating: source agents, anti-hype, judge. Returns, in order, the rating dict or the exception."""
    radar_graph.TECH_CONCURRENCY = parallel
    return asyncio.run(radar_graph.rate_all(radar_graph.build_graph(chat_model), candidates, on_done))


def rate_classic(candidates, llm, on_done=lambda i, c: None):
    """The old single prompt: one call per technology."""
    out = []
    for i, c in enumerate(candidates):
        try:
            out.append({"answer": ai_service.classify_technology(llm, c), "rule_notes": [], "scorecards": [], "errors": [], "risk_memo": ""})
        except Exception as error:
            out.append(error)
        on_done(i, c)
    return out


def outcome(item, candidate, rating):
    base = {"name": item["name"], "expected": item["ring"], "basis": item["basis"], "lanes": lanes_with_data(candidate)}
    if isinstance(rating, BaseException):
        return {**base, "predicted": None, "error": f"{type(rating).__name__}: {rating}"}
    answer = rating["answer"]
    ring, _, notes = pipeline.apply_rules(candidate, answer)
    return {**base, "predicted": ring, "llm_ring": answer.get("ring"), "notes": rating["rule_notes"] + notes,
            "scored_lanes": {c["lane"]: c["confidence"] for c in rating["scorecards"] if c["status"] == "scored"},
            "reason": answer.get("reason", ""), "memo": rating.get("risk_memo", ""), "errors": rating["errors"]}


def summarize(outcomes):
    done = [o for o in outcomes if o["predicted"]]
    distance = lambda o: abs(RINGS.index(o["predicted"]) - RINGS.index(o["expected"]))  # noqa: E731
    return {"items": len(outcomes), "failed": len(outcomes) - len(done),
            "exact": sum(1 for o in done if o["predicted"] == o["expected"]), "within_one": sum(1 for o in done if distance(o) <= 1),
            "by_ring": {r: [sum(1 for o in done if o["expected"] == r and o["predicted"] == r), sum(1 for o in outcomes if o["expected"] == r)]
                        for r in RINGS},
            "by_basis": {b: [sum(1 for o in done if o["basis"] == b and o["predicted"] == o["expected"]), sum(1 for o in outcomes if o["basis"] == b)]
                         for b in sorted({o["basis"] for o in outcomes})},
            "matrix": {e: {p: sum(1 for o in done if o["expected"] == e and o["predicted"] == p) for p in RINGS} for e in RINGS}}


def format_report(meta, outcomes, summary):
    n = summary["items"]
    lines = [f"model {meta['model']} | mode {meta['mode']} | host {meta['host']} | {n} technologies in {meta['seconds']:.0f} s "
             f"({meta['seconds'] / max(n, 1):.1f} s each)",
             f"exact ring: {summary['exact']}/{n} = {summary['exact'] / max(n, 1):.0%} | within one ring: {summary['within_one']}/{n} = "
             f"{summary['within_one'] / max(n, 1):.0%} | failed calls: {summary['failed']}",
             "right per gold ring: " + ", ".join(f"{r} {a}/{b}" for r, (a, b) in summary["by_ring"].items() if b),
             "right per basis:     " + ", ".join(f"{k} {a}/{b}" for k, (a, b) in summary["by_basis"].items()),
             "", "confusion matrix (rows = gold ring, columns = predicted)", "            " + "".join(f"{r:>8}" for r in RINGS)]
    lines += [f"  {e:<9} " + "".join(f"{summary['matrix'][e][p]:>8}" for p in RINGS) for e in RINGS]
    lines += ["", "technology                  gold     predicted  lanes with data"]
    for o in outcomes:
        mark = "ok  " if o["predicted"] == o["expected"] else "MISS" if o["predicted"] else "FAIL"
        lines.append(f"{mark} {o['name']:<24} {o['expected']:<8} {str(o['predicted'] or o.get('error', ''))[:34]:<10} {','.join(o['lanes']) or '-'}")
        if mark != "ok  " and o.get("notes"):
            lines.append(f"       rules: {'; '.join(o['notes'])[:150]}")
    return "\n".join(lines)


def save_result(meta, outcomes, summary, directory=None):
    directory = directory or os.path.join(EVAL_DIR, "results")
    os.makedirs(directory, exist_ok=True)
    stem = f"{re.sub(r'[^A-Za-z0-9.]+', '-', meta['model'])}-{meta['mode']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json.dump({"meta": meta, "summary": summary, "outcomes": outcomes}, open(os.path.join(directory, stem + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(os.path.join(directory, stem + ".txt"), "w", encoding="utf-8") as f:
        f.write(format_report(meta, outcomes, summary) + "\n")
    return os.path.join(directory, stem)


def compare(directory=None):
    """The newest saved result of every (model, mode), side by side."""
    newest = {}
    for path in sorted(glob.glob(os.path.join(directory or os.path.join(EVAL_DIR, "results"), "*.json"))):
        r = json.load(open(path, encoding="utf-8"))
        newest[(r["meta"]["model"], r["meta"]["mode"])] = r
    if not newest:
        return "no saved results yet: run eval/run_gold.py first"
    keys = sorted(newest)
    names = list(dict.fromkeys(o["name"] for r in newest.values() for o in r["outcomes"]))
    head = f"{'technology':<24} {'gold':<7}" + "".join(f"{m[:14] + '/' + mode[:3]:<19}" for m, mode in keys)
    rows = [head, "-" * len(head)]
    for name in names:
        cells = []
        for k in keys:
            o = next((o for o in newest[k]["outcomes"] if o["name"] == name), None)
            cells.append("-" if o is None else f"{o['predicted'] or 'FAIL'}{'' if o['predicted'] == o['expected'] else ' x'}")
        gold = next(o["expected"] for r in newest.values() for o in r["outcomes"] if o["name"] == name)
        rows.append(f"{name:<24} {gold:<7}" + "".join(f"{c:<19}" for c in cells))
    rows.append("-" * len(head))
    rows.append(f"{'exact ring':<32}" + "".join(f"{newest[k]['summary']['exact']}/{newest[k]['summary']['items']:<17}" for k in keys))
    rows.append(f"{'seconds per technology':<32}" + "".join(f"{newest[k]['meta']['seconds'] / max(newest[k]['summary']['items'], 1):<19.1f}" for k in keys))
    return "\n".join(rows)


# ------------------------------------------------------------------ are the gold labels still true?
def verify_labels(items):
    """Re-test every item's `check` against a live source. Returns [(name, "PASS" | "FAIL" | "manual" | "error", detail)]."""
    cncf, today, out = None, date.today(), []
    for item in items:
        check, status, detail = item["check"], "PASS", ""
        try:
            kind = check["type"]
            if kind == "manual":
                status, detail = "manual", "a documented fact, see " + item["source"]
            elif kind == "eol":
                row = next(r for r in _get(f"https://endoflife.date/api/{check['product']}.json") if str(r["cycle"]) == check["cycle"])
                eol = row["eol"]
                ended = eol is True or (isinstance(eol, str) and eol <= today.isoformat())
                status, detail = ("PASS" if ended else "FAIL"), f"{check['product']} {check['cycle']} end of life: {eol}"
            elif kind == "github_archived":
                archived = _get(f"https://api.github.com/repos/{check['repo']}").get("archived")
                status, detail = ("PASS" if archived else "FAIL"), f"{check['repo']} archived: {archived}"
            elif kind == "github_active":
                repo = _get(f"https://api.github.com/repos/{check['repo']}")
                idle = (today - datetime.strptime(repo["pushed_at"][:10], "%Y-%m-%d").date()).days
                good = not repo["archived"] and repo["stargazers_count"] >= check["min_stars"] and idle <= check["max_days_since_push"]
                status, detail = ("PASS" if good else "FAIL"), f"{check['repo']}: {repo['stargazers_count']} stars, pushed {idle} days ago"
            elif kind == "docker_pulls":
                pulls = _get(f"https://hub.docker.com/v2/repositories/{check['image']}/")["pull_count"]
                status, detail = ("PASS" if pulls >= check["min"] else "FAIL"), f"{check['image']}: {pulls:,} pulls"
            elif kind == "npm_downloads":
                downloads = _get(f"https://api.npmjs.org/downloads/point/last-month/{check['package']}")["downloads"]
                status, detail = ("PASS" if downloads >= check["min"] else "FAIL"), f"{check['package']}: {downloads:,} downloads last month"
            elif kind == "cncf":
                if cncf is None:
                    import yaml
                    raw = requests.get("https://raw.githubusercontent.com/cncf/landscape/master/landscape.yml", timeout=60).text
                    cncf = {}

                    def walk(node):
                        if isinstance(node, dict):
                            if node.get("project") and node.get("name"):
                                cncf[node["name"]] = node["project"]
                            for v in node.values():
                                walk(v)
                        elif isinstance(node, list):
                            for v in node:
                                walk(v)

                    walk(yaml.safe_load(raw))
                level = cncf.get(check["name"])
                status, detail = ("PASS" if level == check["level"] else "FAIL"), f"CNCF level of {check['name']}: {level}"
            else:
                status, detail = "error", f"unknown check type {kind}"
        except Exception as error:
            status, detail = "error", f"{type(error).__name__}: {error}"
        out.append((item["name"], status, detail))
    return out


# ------------------------------------------------------------------ command line
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=config.DEFAULT_MODEL)
    ap.add_argument("--host", default=config.DEFAULT_OLLAMA_HOST)
    ap.add_argument("--mode", choices=["agents", "classic"], default="agents")
    ap.add_argument("--rings", help="only these gold rings, e.g. Adopt,Hold")
    ap.add_argument("--names", help="only these technologies, comma separated")
    ap.add_argument("--limit", type=int, help="at most this many technologies per gold ring")
    ap.add_argument("--parallel", type=int, default=2, help="technologies rated at once (default 2)")
    ap.add_argument("--refresh", action="store_true", help="collect the evidence again instead of reusing the saved one")
    ap.add_argument("--evidence-only", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args(argv)

    if args.compare:
        print(compare())
        return 0
    items = load_gold(rings=args.rings.split(",") if args.rings else None,
                      names={n.strip().lower() for n in args.names.split(",")} if args.names else None, limit=args.limit)
    if args.verify:
        results = verify_labels(items)
        for name, status, detail in results:
            print(f"{status:<7} {name:<24} {detail}")
        failed = [r for r in results if r[1] in ("FAIL", "error")]
        print(f"\n{len(results) - len(failed)}/{len(results)} labels confirmed or documented, {len(failed)} to look at")
        return 1 if failed else 0

    print(f"collecting evidence for {len(items)} technologies (saved in data/eval/evidence)...", flush=True)
    candidates = []
    for i, item in enumerate(items, start=1):
        collected = collect_evidence(item, refresh=args.refresh)
        candidate = build_candidate(item, collected)
        candidates.append(candidate)
        flag = f"  (incomplete: {collected['errors'][0][:80]})" if collected["errors"] else ""
        print(f"  {i:>2}/{len(items)} {item['name']:<24} gold {item['ring']:<7} lanes: {','.join(lanes_with_data(candidate)) or '-'}{flag}", flush=True)
    if args.evidence_only:
        return 0

    started, done = time.time(), []
    progress = lambda i, c: (done.append(c), print(f"  rated {len(done)}/{len(candidates)}: {c['name']}", flush=True))  # noqa: E731
    print(f"rating with {args.model} at {args.host} ({args.mode})...", flush=True)
    if args.mode == "agents":
        ratings = rate_agents(candidates, radar_graph.make_chat_model(args.host, args.model), args.parallel, progress)
    else:
        ratings = rate_classic(candidates, ai_service.LLM(args.host, args.model), progress)
    outcomes = [outcome(i, c, r) for i, c, r in zip(items, candidates, ratings)]
    summary = summarize(outcomes)
    meta = {"model": args.model, "host": args.host, "mode": args.mode, "seconds": time.time() - started, "run_at": datetime.now().isoformat(timespec="seconds")}
    print("\n" + format_report(meta, outcomes, summary))
    print("\nsaved:", save_result(meta, outcomes, summary) + ".json / .txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
