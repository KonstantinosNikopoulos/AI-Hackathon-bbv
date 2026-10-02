"""Facts measured by code about ONE technology, written into the rating prompt (prompts/classify.md) after the evidence.

The rules of the radar are in that prompt, in words: an archived repository or a license that blocks commercial reuse is Hold, a project
younger than 6 months is at most Assess, and a technology the model knows to be a standard is Adopt. The prompt needs the facts to apply
them, and a model cannot count days or read a license id reliably, so code measures them. Nothing here changes a ring afterwards: a first
version did (code vetoes from scores a 4B model had guessed from headlines) and rejected Linux, PostgreSQL and Kubernetes. Pure Python, no LLM."""
from datetime import date, datetime

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
LICENSE_RISK_HIGH = 8     # from this risk on the facts say "HIGH licensing risk" (AGPL, SSPL, BUSL, Elastic)


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


def _days_ago(day, today):
    try:
        return (today - datetime.strptime(str(day)[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return None


def _short(n):
    """118000 -> '118k', 11707770270 -> '11.7B'."""
    for suffix, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size * 0.9995:
            return f"{n / size:.1f}".removesuffix(".0") + suffix
    return str(n)


def own_repo(candidate, today=None):
    """Its main repository (the most starred of the GitHub repositories that ARE the technology) as a dict, or None."""
    today = today or date.today()
    own_urls = set(candidate.get("own_repos") or [])
    repos = [e for e in candidate.get("evidence") or [] if e.get("source") == "GitHub" and e.get("url") in own_urls]
    if not repos:
        return None
    main = max(repos, key=lambda e: (e.get("meta") or {}).get("stars") or 0)
    m = main.get("meta") or {}
    return {"repo": m.get("full_name") or main["title"], "stars": m.get("stars"), "created": m.get("created_at") or None,
            "age_days": _days_ago(m.get("created_at"), today), "days_since_push": _days_ago(m.get("pushed_at"), today),
            "license": m.get("license"), "archived": bool(m.get("archived")),
            "licensing_risk": license_risk(m["license"]) if m.get("license") is not None else None}


def format_facts(candidate, today=None):
    """The facts as the lines the prompt reads after the evidence."""
    own = own_repo(candidate, today)
    if not own:
        return "- No GitHub repository of its own was found for it (this says nothing about how old or how used it is)."
    age = "" if own["age_days"] is None else f" ({own['age_days']:,} days ago)"
    push = "unknown" if own["days_since_push"] is None else f"{own['days_since_push']} days ago"
    if own["license"] is None:
        license_text = "unknown"
    elif (own["licensing_risk"] or 0) >= LICENSE_RISK_HIGH:
        license_text = f"{own['license']} (HIGH licensing risk: blocks commercial reuse)"
    else:
        license_text = own["license"]
    stars = "unknown" if own["stars"] is None else f"{_short(own['stars'])} stars"
    return (f"- Its own GitHub repository {own['repo']}: {stars}, created {own['created'] or 'unknown'}{age}, last push {push}, "
            f"license {license_text}, " + ("ARCHIVED (read-only: nobody maintains it)." if own["archived"] else "not archived."))
