"""Facts and schemas for the five rating agents, one per source (GitHub, Y Combinator, Hacker News, RSS feeds and the
package registries npm, PyPI, Maven Central and Docker Hub).

Pure Python, no LLM. Every number the agents see is computed here, so the model only has to judge
the numbers and cannot invent them."""
from collections import Counter
from datetime import date, datetime
from typing import Literal, NamedTuple, get_args
from urllib.parse import urlparse

from config import REGISTRY_SOURCE

Lane = Literal["github", "ycombinator", "hackernews", "rss", "packages"]
LANES: tuple[Lane, ...] = get_args(Lane)
LANE_SOURCE: dict[Lane, str] = {"github": "GitHub", "ycombinator": "Y Combinator", "hackernews": "Hacker News",
                                "rss": "RSS feeds", "packages": REGISTRY_SOURCE}

# Every score is an integer from 0 to 10. SCORES_BY_LANE are written by the agent (the LLM);
# COMPUTED_SCORES are calculated in code from the data and added to the scorecard.
SCORE_MIN, SCORE_MAX = 0, 10
SCORES_BY_LANE: dict[Lane, tuple[str, ...]] = {
    "github": ("developer_velocity", "project_health"),
    "hackernews": ("community_support", "developer_friction"),
    "rss": ("enterprise_traction", "maturity"),
    "ycombinator": ("market_momentum", "hype_risk"),
    "packages": ("production_usage_score", "integration_velocity_score"),
}
COMPUTED_SCORES: dict[Lane, tuple[str, ...]] = {"github": ("licensing_risk",)}
# The Package registries agent follows its own spec (prompts/rate_packages.md): scores from 1 to 10, and no `confidence` in
# its answer, because how far its numbers can be trusted is known from the data (Facts.confidence_cap), not from the model.
SCORE_FLOOR: dict[Lane, int] = {"packages": 1}
CODE_CONFIDENCE_LANES = frozenset({"packages"})
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
    unscored: tuple[str, ...] = ()   # scores the data cannot support at all: unknown, whatever the model answered


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
    floor = SCORE_FLOOR.get(lane, SCORE_MIN)
    scores = {name: {"type": "integer", "minimum": floor, "maximum": SCORE_MAX} for name in SCORES_BY_LANE[lane]}
    confidence = {} if lane in CODE_CONFIDENCE_LANES else {"confidence": {"type": "string", "enum": ["high", "medium", "low"]}}
    return {
        "title": f"scorecard_{lane}",
        "type": "object",
        "properties": {"summary": {"type": "string"}, **scores, **confidence},
        "required": ["summary", *scores, *confidence],
    }


def clean_scores(answer, names, limits=None, floor=SCORE_MIN):
    """Models sometimes ignore minimum/maximum: clamp into floor-10 (and below `limits`), and map anything that is not a
    number to None."""
    limits = limits or {}
    scores = {}
    for name in names:
        value = answer.get(name)
        scores[name] = (max(floor, min(SCORE_MAX, limits.get(name, SCORE_MAX), round(value)))
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


# Package registries. The evidence items come from services/registry_service.py and only hold packages that are known to BE
# the technology (curated, repository link, Docker official image), so a name collision cannot pose as its downloads.
PACKAGES_YOUNG_MONTHS = 6   # a package with less download history than this cannot show "sustained" use ...
PACKAGES_YOUNG_MAX = 7      # ... so production_usage_score stays below 8 (the prompt says so, code enforces it)
REGISTRY_LABEL = {"npm": "npm", "pypi": "PyPI", "maven": "Maven Central", "docker": "Docker Hub"}
MATCH_TEXT = {"curated": "curated by bbv", "verified": "its repository link is the technology's GitHub repository",
              "official": "Docker official image", "owner": "image of that name under the GitHub repository's owner"}


def _count(n):
    """1234567 -> '1.2M'. The registries give exact numbers; the agent only needs the size."""
    if n is None:
        return "unknown"
    for suffix, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size * 0.9995:   # 999,999 is "1M", not "1000K"
            return f"{n / size:.1f}".removesuffix(".0") + suffix
    return str(n)


def _growth(series):
    """Month-over-month change in percent; None where the month before had no downloads."""
    return [round((b - a) / a * 100) if a else None for a, b in zip(series, series[1:])]


def _history_months(m, today):
    """How long the package has been downloaded: the months with downloads, else the months since its first release."""
    if m.get("monthly"):
        return len(m["monthly"])
    age = _age_days(m.get("first_release"), today)
    return None if age is None else age // 30


def _package_line(m):
    head = f"{REGISTRY_LABEL[m['registry']]} {m['package']}"
    if m["registry"] == "docker":
        head += " (OFFICIAL image)" if m.get("official") else " (unofficial image)"
        facts = [f"lifetime pulls {_count(m.get('lifetime'))}", f"stars {_show(m.get('stars'))}",
                 f"registered {_show(m.get('first_release'))}", f"last updated {_show(m.get('latest_release'))}"]
    else:
        facts = ["no download counts are published" if m["registry"] == "maven"
                 else f"downloads in the last 30 days {_count(m.get('last_30d'))}"]
        series = m.get("monthly") or []
        if len(series) >= 2:
            facts.append("monthly downloads, oldest to newest: " + ", ".join(_count(v) for v in series))
            facts.append("month-over-month: " + ", ".join("n/a" if g is None else f"{g:+d}%" for g in _growth(series)))
        if m.get("window_total") is not None:
            facts.append(f"total over the last {m['window_days']} days {_count(m['window_total'])}")
        packages, repos = m.get("dependent_packages"), m.get("dependent_repos")
        facts.append("dependents unknown" if packages is None and repos is None
                     else f"dependents: {_count(packages)} packages, {_count(repos)} GitHub repositories")
        facts.append(f"{_show(m.get('versions'))} versions, latest {_show(m.get('latest'))} "
                     f"released {_show(m.get('latest_release'))}")
    return f"- {head} | match: {MATCH_TEXT[m['match']]} | " + " | ".join(facts)


def _packages(candidate, evidence, today):
    records = [_meta(e) for e in evidence]
    lines = ["Package registry data for this technology (npm, PyPI, Maven Central, Docker Hub). Every package below is known to "
             "be this technology. Numbers were measured by code. Only Docker Hub reports lifetime numbers: npm and PyPI show "
             "a window of recent days, and Maven Central publishes no download counts at all:"]
    lines += [_package_line(m) for m in records]
    measured = any(m.get(k) is not None for m in records for k in ("last_30d", "lifetime", "dependent_packages", "dependent_repos"))
    ages = [h for m in records if (h := _history_months(m, today)) is not None]
    # high: a real trend (3+ months of downloads). A single month, a Docker pull count or a dependents count is medium.
    cap = "low" if not measured else "high" if any(len(m.get("monthly") or []) >= 3 for m in records) else "medium"
    metrics = {"packages": [dict(m, month_over_month=_growth(m["monthly"])) if m.get("monthly") else m for m in records]}
    # Growth needs two months of downloads. Without them the score is unknown, whatever the model answered.
    unscored = () if any(len(m.get("monthly") or []) >= 2 for m in records) else ("integration_velocity_score",)
    limits = {"production_usage_score": PACKAGES_YOUNG_MAX} if ages and max(ages) < PACKAGES_YOUNG_MONTHS else {}
    return Facts("\n".join(lines), metrics, {}, cap, limits, unscored)


_FACTS = {"github": _github, "hackernews": _hackernews, "ycombinator": _ycombinator, "rss": _rss, "packages": _packages}


def lane_facts(lane, candidate, evidence, today=None):
    return _FACTS[lane](candidate, evidence, today or date.today())
