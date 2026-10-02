"""What code knows about ONE technology before a model reads a word: how long it has existed, how big it is, whether it is
still maintained. Pure Python, no LLM (like radar_lanes.py).

The record has two jobs:
 1. The judge reads it as facts, together with the technology's STANDING. An "established" technology (years old, with real
    traction) is judged with what the model knows about it (Linux is a standard, Python 2 is retired even when the evidence is a
    few old headlines). For every other technology the model cannot know it from memory, so it judges from the evidence only.
 2. apply_guards enforces the few rules that are FACTS: an archived repository, a license nobody can use, a technology too new to
    be proven. What a model only guessed from headlines (friction, hype, maturity) is NOT a rule: the first version of the
    rules vetoed Linux, PostgreSQL and Kubernetes because their Hacker News titles sounded critical.

The numbers below are round figures chosen from the meaning of the rings, not fitted to eval/gold_radar.json. The ADOPT scale (ten times
the traction that makes a technology established) was added after the first results on it; eval/heldout_radar.json is the check."""
from datetime import date, datetime

from config import REGISTRY_SOURCE
from services.radar_lanes import license_risk

NEW_DAYS = 183                         # first seen less than about 6 months ago: nothing is proven yet, at most ASSESS
ESTABLISHED_DAYS = 3 * 365             # first seen 3+ years ago, and with traction (below): ADOPT becomes possible
TRACTION_STARS = 1000                  # traction: a repository with this many stars, or ...
TRACTION_MONTHLY_DOWNLOADS = 100_000   # ... a package downloaded this often in a month, or ...
TRACTION_PULLS = 10_000_000            # ... a Docker image pulled this often, or ...
TRACTION_DEPENDENTS = 1000             # ... this many packages that depend on it, or ...
TRACTION_ARTICLES = 3                  # ... when it has no repository to ask: this many stories or articles about it
SCALE_FACTOR = 10                      # "widely used" (ADOPT) is ten times the traction that makes a technology established
HOLD_IF_LICENSING_RISK_ABOVE = 7       # radar_lanes.license_risk: 9 = AGPL, SSPL and the like, 7 = no license at all

# new < emerging < established < widespread. "unknown": nothing shows how old it is.
STANDINGS = ("new", "emerging", "established", "widespread", "unknown")
LONG_FORM = {"new": "new (first seen less than 6 months ago, nothing is proven yet)",
             "emerging": "emerging (not yet proven by years of real use)",
             "established": "established (years old, with real traction, but not used at scale)",
             "widespread": "widespread (years old and used at scale)",
             "unknown": "unknown (nothing shows how old it is)"}


def is_established(record):
    """Years old with real traction: the model may judge it with what it knows, and the skeptic has nothing to add."""
    return record["standing"] in ("established", "widespread")


def _days_ago(day, today):
    try:
        return (today - datetime.strptime(str(day)[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return None


def _meta(e):
    return e.get("meta") or {}


def _short(n):
    """118000 -> '118k', 11707770270 -> '11.7B'."""
    for suffix, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size * 0.9995:
            return f"{n / size:.1f}".removesuffix(".0") + suffix
    return str(n)


def build_record(candidate, today=None):
    """The facts about one candidate (pipeline.merge_candidates' dict). Every field is measured, nothing is judged."""
    today = today or date.today()
    own_urls = set(candidate.get("own_repos") or [])
    evidence = candidate.get("evidence") or []
    repos = [(e, _meta(e)) for e in evidence if e.get("source") == "GitHub" and e.get("url") in own_urls]
    packages = [_meta(e) for e in evidence if e.get("source") == REGISTRY_SOURCE]
    talk = [e for e in evidence if e.get("source") not in ("GitHub", REGISTRY_SOURCE)]   # stories and articles, matched by name

    main = max(repos, key=lambda r: r[1].get("stars") or 0, default=None)   # its biggest own repository speaks for the technology
    own = None
    if main:
        m = main[1]
        license_id = m.get("license")
        own = {"repo": m.get("full_name") or main[0]["title"], "stars": m.get("stars"), "created": m.get("created_at") or None,
               "days_since_push": _days_ago(m.get("pushed_at"), today), "license": license_id, "archived": bool(m.get("archived")),
               "licensing_risk": license_risk(license_id) if license_id is not None else None}

    # When was it first seen? The oldest date that is TIED to the technology: its main repository or a verified package. Other
    # repositories of the same name may be unrelated, and stories and articles are matched by name (a telescope is not a Kubernetes
    # exporter), so those only count when nothing better exists, and only as proof that it is OLD: a story from 2015 shows that
    # the technology existed in 2015, but a scan only sees recent items, so "the oldest story is from last week" shows nothing.
    tied = [d for d in [own and own["created"]] + [p.get("first_release") for p in packages] if _days_ago(d, today) is not None]
    by = "repository or package" if tied else None
    dates = tied
    if not dates:
        dates = [e["date"] for e in talk if (_days_ago(e.get("date"), today) or 0) >= NEW_DAYS]   # "or 0": no date, no proof
        by = "stories and articles" if dates else None
    first_seen = min(dates) if dates else None
    age = _days_ago(first_seen, today) if first_seen else None

    def measured(factor):
        """What the numbers show, at `factor` times the traction thresholds."""
        found = []
        if own and (own["stars"] or 0) >= TRACTION_STARS * factor:
            found.append(f"{_short(own['stars'])} stars")
        for p in packages:
            label = f"{p.get('registry', 'registry')} {p.get('package', '?')}"
            if (p.get("last_30d") or 0) >= TRACTION_MONTHLY_DOWNLOADS * factor:
                found.append(f"{label}: {_short(p['last_30d'])} downloads in 30 days")
            if (p.get("lifetime") or 0) >= TRACTION_PULLS * factor:
                found.append(f"{label}: {_short(p['lifetime'])} pulls")
            if (p.get("dependent_packages") or 0) >= TRACTION_DEPENDENTS * factor:
                found.append(f"{label}: {_short(p['dependent_packages'])} dependent packages")
        return found

    traction, scale = measured(1), measured(SCALE_FACTOR)
    if not own and len(talk) >= TRACTION_ARTICLES:   # stories and articles are matched by name: they only count with no repository to ask
        traction.append(f"{len(talk)} stories or articles about it")

    if age is None:
        standing = "unknown"
    elif age < NEW_DAYS:
        standing = "new"
    elif age >= ESTABLISHED_DAYS and traction:
        standing = "widespread" if scale else "established"
    else:
        standing = "emerging"
    return {"own_repo": own, "packages": packages, "first_seen": first_seen, "first_seen_from": by, "age_days": age,
            "traction": traction, "scale": scale, "standing": standing, "items": len(talk) + len(repos),
            "sources": sorted({e["source"] for e in evidence if e.get("source") != REGISTRY_SOURCE})}


def format_record(record):
    """The facts as text for the judge."""
    own, lines = record["own_repo"], []
    age = record["age_days"]
    lines.append(f"- Standing: {LONG_FORM[record['standing']]}."
                 + (f" First seen {record['first_seen']} ({age / 365.25:.1f} years ago), from its {record['first_seen_from']}." if age is not None else "")
                 + (f" Traction: {'; '.join(record['traction'])}." if record["traction"] else " No traction measured."))
    if own:
        pushed = "unknown" if own["days_since_push"] is None else f"{own['days_since_push']} days ago"
        lines.append(f"- Its own GitHub repository {own['repo']}: {_short(own['stars']) if own['stars'] is not None else 'unknown'} stars, created "
                     f"{own['created'] or 'unknown'}, last push {pushed}, license {own['license'] or 'unknown'}, "
                     f"{'ARCHIVED (read-only, unmaintained)' if own['archived'] else 'not archived'}.")
    else:
        lines.append("- No GitHub repository of its own was found for it.")
    for p in record["packages"][:4]:
        facts = []
        if p.get("last_30d") is not None:
            facts.append(f"{_short(p['last_30d'])} downloads in the last 30 days")
        if p.get("lifetime") is not None:
            facts.append(f"{_short(p['lifetime'])} pulls in total")
        if p.get("dependent_packages") is not None:
            facts.append(f"{_short(p['dependent_packages'])} dependent packages")
        lines.append(f"- Package {p.get('registry', 'registry')} {p.get('package', '?')}" + (" (official image)" if p.get("official") else "") + ": "
                     + (", ".join(facts) or "no numbers") + ".")
    return "\n".join(lines)


def matrix_rules():
    """The guards in words, for the judge prompt. apply_guards enforces the same numbers, so the model knows what code will do."""
    return [
        f"If the technology's own repository has a risky license (licensing_risk above {HOLD_IF_LICENSING_RISK_ABOVE}: AGPL, SSPL, no license), "
        "the ring must be HOLD.",
        "If the technology's own repository is archived, the ring must be HOLD: nobody maintains it any more.",
        f"A technology first seen less than {NEW_DAYS // 30} months ago can be at most ASSESS: nothing is proven yet.",
        f"ADOPT needs the standing 'widespread': first seen {ESTABLISHED_DAYS // 365}+ years ago and used at scale "
        f"({_short(TRACTION_STARS * SCALE_FACTOR)}+ stars, or {_short(TRACTION_MONTHLY_DOWNLOADS * SCALE_FACTOR)}+ downloads a month, "
        f"{_short(TRACTION_PULLS * SCALE_FACTOR)}+ image pulls, {_short(TRACTION_DEPENDENTS * SCALE_FACTOR)}+ dependent packages). "
        "Otherwise it can be at most TRIAL.",
    ]


def apply_guards(ring, record):
    """The ring after the facts have had their say. Returns (ring, notes), one note per change."""
    notes, order = [], ["Adopt", "Trial", "Assess", "Hold"]

    def move(new, why):
        nonlocal ring
        if new != ring:
            notes.append(f"{ring} → {new}: {why}")
            ring = new

    own = record["own_repo"]
    if own and own["licensing_risk"] is not None and own["licensing_risk"] > HOLD_IF_LICENSING_RISK_ABOVE:
        move("Hold", f"licensing_risk is {own['licensing_risk']} (license {own['license']})")
    elif own and own["archived"]:
        move("Hold", f"the repository {own['repo']} is archived")
    elif record["standing"] == "new" and order.index(ring) < order.index("Assess"):
        move("Assess", f"first seen only {record['age_days']} days ago, nothing is proven yet")
    elif ring == "Adopt" and record["standing"] != "widespread":
        move("Trial", "ADOPT needs years of use at scale, here the standing is " + LONG_FORM[record["standing"]])
    return ring, notes


def fatal_flaws(record):
    """The measurable reasons to reject, for the anti-hype agent: facts, not opinions."""
    own, flaws = record["own_repo"], []
    if own and own["licensing_risk"] is not None and own["licensing_risk"] > HOLD_IF_LICENSING_RISK_ABOVE:
        flaws.append(f"licensing_risk is {own['licensing_risk']} (license {own['license']})")
    if own and own["archived"]:
        flaws.append(f"the repository {own['repo']} is archived")
    return flaws
