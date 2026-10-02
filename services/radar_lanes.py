"""Facts and schemas for the four rating agents, one per source (GitHub, Y Combinator, Hacker News, RSS feeds).

Pure Python, no LLM. Every number the agents see is computed here, so the model only has to judge
the numbers and cannot invent them."""
from collections import Counter
from datetime import date, datetime
from typing import Literal, NamedTuple, get_args
from urllib.parse import urlparse

Lane = Literal["github", "ycombinator", "hackernews", "rss"]
LANES: tuple[Lane, ...] = get_args(Lane)
LANE_SOURCE: dict[Lane, str] = {"github": "GitHub", "ycombinator": "Y Combinator",
                                "hackernews": "Hacker News", "rss": "RSS feeds"}

# Every score is an integer from 0 to 10. SCORES_BY_LANE are written by the agent (the LLM);
# COMPUTED_SCORES are calculated in code from the data and added to the scorecard.
SCORE_MIN, SCORE_MAX = 0, 10
SCORES_BY_LANE: dict[Lane, tuple[str, ...]] = {
    "github": ("developer_velocity", "project_health"),
    "hackernews": ("community_support", "developer_friction"),
    "rss": ("enterprise_traction", "maturity"),
    "ycombinator": ("market_momentum", "hype_risk"),
}
COMPUTED_SCORES: dict[Lane, tuple[str, ...]] = {"github": ("licensing_risk",)}
# For these, a HIGHER number is WORSE (for all other scores, higher is better).
RISK_SCORES = frozenset({"developer_friction", "hype_risk", "licensing_risk"})

# GitHub's SPDX id -> how risky it is for a consultancy that builds software for customers (0-10).
LICENSE_RISK = {
    "MIT": 1, "Apache-2.0": 1, "BSD-2-Clause": 1, "BSD-3-Clause": 1, "ISC": 1, "Unlicense": 1, "0BSD": 1,
    "CC0-1.0": 1, "Zlib": 1, "BSL-1.0": 1,
    "MPL-2.0": 3, "EPL-2.0": 3, "EPL-1.0": 3, "LGPL-2.1": 3, "LGPL-3.0": 3,
    "GPL-2.0": 6, "GPL-3.0": 6,
    "AGPL-3.0": 9, "SSPL-1.0": 9, "BUSL-1.1": 9, "Elastic-2.0": 9,
}
LICENSE_RISK_NONE = 7     # the repository has no license at all: legally, nobody may reuse it
LICENSE_RISK_UNKNOWN = 5  # GitHub could not classify the license (NOASSERTION) or we do not know the id


CONFIDENCE_ORDER = ("low", "medium", "high")
COMPUTED_NAMES = frozenset(n for names in COMPUTED_SCORES.values() for n in names)
YOUNG_REPO_DAYS = 180   # the GitHub collector only returns repositories created in the last N days, so these are all young
YOUNG_REPO_HEALTH_MAX = 6


class Facts(NamedTuple):
    text: str                  # what the agent reads
    metrics: dict              # the same numbers as data, stored in the scorecard
    computed: dict[str, int]   # scores calculated in code, e.g. {"licensing_risk": 9}
    confidence_cap: str        # the agent may not claim more confidence than the amount of data allows
    limits: dict[str, int]     # upper bounds the prompt promises but a small model may break, e.g. {"project_health": 6}


def license_risk(spdx):
    """spdx: GitHub's spdx_id, or "none" when the repository has no license."""
    if spdx == "none":
        return LICENSE_RISK_NONE
    if spdx in LICENSE_RISK:
        return LICENSE_RISK[spdx]
    for prefix, risk in (("AGPL", 9), ("LGPL", 3), ("GPL", 6)):
        if str(spdx).upper().startswith(prefix):
            return risk
    return LICENSE_RISK_UNKNOWN


def lane_schema(lane):
    """JSON schema for the agent's answer. `summary` comes first so the model writes its reasoning before the scores."""
    scores = {name: {"type": "integer", "minimum": SCORE_MIN, "maximum": SCORE_MAX} for name in SCORES_BY_LANE[lane]}
    return {
        "title": f"scorecard_{lane}",
        "type": "object",
        "properties": {"summary": {"type": "string"}, **scores,
                       "confidence": {"type": "string", "enum": ["high", "medium", "low"]}},
        "required": ["summary", *scores, "confidence"],
    }


def clean_scores(answer, names, limits=None):
    """Models sometimes ignore minimum/maximum: clamp into 0-10 (and below `limits`), and map anything that is not a
    number to None."""
    limits = limits or {}
    scores = {}
    for name in names:
        value = answer.get(name)
        scores[name] = (max(SCORE_MIN, min(SCORE_MAX, limits.get(name, SCORE_MAX), round(value)))
                        if isinstance(value, (int, float)) and not isinstance(value, bool) else None)
    return scores


def cap_confidence(claimed, cap):
    """The lower of what the model claims and what the data allows."""
    return min(claimed, cap, key=CONFIDENCE_ORDER.index)


# ------------------------------------------------------------------ helpers
def _age_days(day, today):
    try:
        return (today - datetime.strptime(str(day)[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return None


def _show(value):
    return "unknown" if value is None or value == "" else value


def _meta(e):
    return e.get("meta") or {}


# ------------------------------------------------------------------ one facts function per source
def _github(candidate, evidence, today):
    own_urls = set(candidate.get("own_repos") or [])
    repos, lines = [], ["GitHub repositories linked to this technology (numbers measured by code):"]
    for e in evidence:
        m = _meta(e)
        age, pushed, stars = _age_days(m.get("created_at"), today), _age_days(m.get("pushed_at"), today), m.get("stars")
        repo = {"repo": m.get("full_name") or e["title"], "own_repo": e["url"] in own_urls, "stars": stars,
                "age_days": age, "stars_per_day": round(stars / max(age, 1), 1) if stars is not None and age is not None else None,
                "days_since_push": pushed, "forks": m.get("forks"), "open_issues": m.get("open_issues"),
                "license": m.get("license"), "archived": m.get("archived")}
        repos.append(repo)
        lines.append(f"- {repo['repo']} | own repo = {'yes' if repo['own_repo'] else 'no'} | language {_show(m.get('language'))} | "
                     f"stars {_show(stars)} | age_days {_show(age)} | stars_per_day {_show(repo['stars_per_day'])} | "
                     f"days_since_push {_show(pushed)} | forks {_show(repo['forks'])} | open_issues {_show(repo['open_issues'])} | "
                     f"license {_show(repo['license'])} | archived {_show(repo['archived'])}")
        lines.append(f"  description: {str(m.get('description') or e.get('text') or '')[:160]}")
    # The license of a repository that only mentions the technology says nothing about the technology: own repos only.
    own = [r for r in repos if r["own_repo"]]
    risks = [license_risk(r["license"]) for r in own if r["license"] is not None]
    computed = {"licensing_risk": max(risks)} if risks else {}
    complete = ("stars", "age_days", "days_since_push", "forks", "open_issues", "license")
    cap = "low" if not own else "high" if all(r[k] is not None for r in own for k in complete) else "medium"
    young = not any(r["age_days"] is not None and r["age_days"] >= YOUNG_REPO_DAYS for r in repos)
    return Facts("\n".join(lines), {"repos": repos}, computed, cap, {"project_health": YOUNG_REPO_HEALTH_MAX} if young else {})


def _hackernews(candidate, evidence, today):
    stories = [(e, _meta(e)) for e in evidence]
    points = [m["points"] for _, m in stories if m.get("points") is not None]
    comments = [m["comments"] for _, m in stories if m.get("comments") is not None]
    domains = sorted({m["domain"] for _, m in stories if m.get("domain")})
    metrics = {"stories": len(stories), "total_points": sum(points), "total_comments": sum(comments),
               "top_points": max(points, default=0), "domains": domains}
    lines = [f"Hacker News stories about this technology ({metrics['stories']} stories, {metrics['total_points']} points and "
             f"{metrics['total_comments']} comments in total). Only titles are available, no comment text:"]
    lines += [f'- "{e["title"]}" | {_show(m.get("points"))} points | {_show(m.get("comments"))} comments | '
              f'{_show(m.get("domain"))} | {_show(e.get("date"))}' for e, m in stories]
    return Facts("\n".join(lines), metrics, {}, "low" if len(stories) < 2 else "medium", {})   # titles only: never high


def _ycombinator(candidate, evidence, today):
    startups = [(e, _meta(e)) for e in evidence]
    batches = [str(m.get("batch")) for _, m in startups if m.get("batch")]
    tags = Counter(t for _, m in startups for t in (m.get("tags") or []))
    metrics = {"startups": len(startups), "batches": batches, "top_tags": [t for t, _ in tags.most_common(5)]}
    lines = [f"Y Combinator startups linked to this technology ({metrics['startups']}):"]
    lines += [f"- {m.get('company') or e['title']} (batch {_show(m.get('batch'))}) | tags: {', '.join(m.get('tags') or []) or 'none'} | "
              f"{str(m.get('one_liner') or e.get('text') or '')[:200]}" for e, m in startups]
    return Facts("\n".join(lines), metrics, {}, "low" if len(startups) <= 2 else "medium", {})   # marketing text: never high


def _rss(candidate, evidence, today):
    sites = [urlparse(e["url"]).netloc.replace("www.", "") for e in evidence]
    dates = sorted(e["date"] for e in evidence if e.get("date"))
    metrics = {"articles": len(evidence), "sites": sorted(set(sites)), "oldest": dates[0] if dates else None,
               "newest": dates[-1] if dates else None}
    lines = [f"Articles about this technology from {len(metrics['sites'])} site(s) ({metrics['articles']} articles):"]
    lines += [f"- {_show(e.get('date'))} | {site} | {e['title']} | {str(e.get('text') or '')[:240]}"
              for e, site in zip(evidence, sites)]
    cap = "low" if len(evidence) < 2 else "high" if len(metrics["sites"]) >= 2 else "medium"
    return Facts("\n".join(lines), metrics, {}, cap, {})


_FACTS = {"github": _github, "hackernews": _hackernews, "ycombinator": _ycombinator, "rss": _rss}


def lane_facts(lane, candidate, evidence, today=None):
    return _FACTS[lane](candidate, evidence, today or date.today())
