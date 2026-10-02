"""Offline test of the facts and the guards (services/radar_record.py): no internet, no Ollama.
Run from the project folder:  python tests/test_record.py"""
import os
import sys
import types
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "ollama" not in sys.modules:
    try:
        import ollama  # noqa: F401
    except ImportError:  # allow running the test without the package
        sys.modules["ollama"] = types.SimpleNamespace(Client=lambda **kw: None)

import config  # noqa: E402
from services import pipeline, radar_record as rr  # noqa: E402

TODAY = date(2026, 10, 2)


def years_ago(n):
    return date(TODAY.year - n, TODAY.month, TODAY.day).isoformat()


def days_ago(n):
    return date.fromordinal(TODAY.toordinal() - n).isoformat()


def repo(i=0, created=None, stars=5000, license="MIT", archived=False, pushed=None, **extra):
    meta = {"name": f"foo{i}", "full_name": f"a/foo{i}", "stars": stars, "created_at": created or years_ago(10), "pushed_at": pushed or days_ago(2),
            "license": license, "archived": archived, "forks": 1, "open_issues": 1}
    return {"source": "GitHub", "title": f"a/foo{i}", "url": f"https://github.com/a/foo{i}", "text": "t", "date": meta["created_at"],
            "meta": {**meta, **extra}}


def talk(source, year, i=0):
    return {"source": source, "title": f"{source} {i}", "url": f"https://x/{source}/{i}", "text": "t", "date": f"{year}-06-01", "meta": {}}


def package(registry="npm", **meta):
    base = {"registry": registry, "package": "foo", "match": "verified", "official": False}
    return {"source": config.REGISTRY_SOURCE, "title": "p", "url": "https://x/p", "text": "t", "date": "2026-09-01", "meta": {**base, **meta}}


def candidate(*evidence, own=True):
    github = [e for e in evidence if e["source"] == "GitHub"]
    return {"name": "Foo", "key": "foo", "what": "A foo.", "quadrant": "Tools", "mentions": len(evidence), "sources": [], "signal": 1,
            "youngest_repo_days": None, "own_repos": [e["url"] for e in github] if own else [], "evidence": list(evidence)}


def record(*evidence, own=True):
    return rr.build_record(candidate(*evidence, own=own), TODAY)


def guard(ring, rec):
    return rr.apply_guards(ring, rec)


# ------------------------------------------------------------------ standing: a ladder from what code can measure
widespread = record(repo(created=years_ago(10), stars=50_000))
assert widespread["standing"] == "widespread" and widespread["age_days"] > 3000 and widespread["traction"] == ["50k stars"]
assert widespread["own_repo"]["repo"] == "a/foo0" and widespread["own_repo"]["days_since_push"] == 2 and widespread["scale"] == ["50k stars"]
established = record(repo(created=years_ago(10), stars=2000))
assert established["standing"] == "established" and established["scale"] == [], "old, and 1000+ stars, but not 10000+"
assert rr.is_established(widespread) and rr.is_established(established)
assert record(repo(created=years_ago(2), stars=50_000))["standing"] == "emerging", "popular but only two years old: not proven by years"
assert record(repo(created=years_ago(10), stars=200))["standing"] == "emerging", "old but nobody uses it: no traction"
assert record(repo(created=years_ago(3), stars=1000))["standing"] == "established", "exactly 3 years and 1000 stars"
new = record(repo(created=days_ago(40), stars=5000))
assert new["standing"] == "new" and new["age_days"] == 40 and not rr.is_established(new)
assert record(repo(created=days_ago(182), stars=5000))["standing"] == "new" and record(repo(created=days_ago(184), stars=5000))["standing"] == "emerging"
assert record(talk("Hacker News", 2026), own=False)["standing"] == "unknown", "this year's headlines show nothing about its age (a scan only sees recent items)"
assert record(talk("Hacker News", 2026), talk("RSS feeds", 2025), own=False)["standing"] == "emerging", "a story older than 6 months: it is not new"
nothing = rr.build_record({"name": "X", "evidence": [{"source": "Y Combinator", "title": "t", "url": "u", "text": "", "date": ""}]}, TODAY)
assert nothing["standing"] == "unknown" and nothing["age_days"] is None and nothing["own_repo"] is None
assert rr.build_record({"name": "X"}, TODAY)["standing"] == "unknown", "a candidate without evidence does not crash"

# the age is TIED to the technology: its biggest repository and its verified packages, not a repository of the same name or a headline
young_big = repo(0, created=days_ago(40), stars=9000)
old_small = repo(1, created=years_ago(12), stars=20)
mixed = record(old_small, young_big)
assert mixed["standing"] == "new" and mixed["own_repo"]["repo"] == "a/foo0", "the most starred repository speaks for the technology"
collision = record(repo(created=days_ago(40), stars=5000), talk("Hacker News", 2013, 1), talk("Hacker News", 2014, 2), talk("Hacker News", 2015, 3))
assert collision["standing"] == "new", "old headlines with the same name (a telescope) must not make a new repository old"
with_package = record(repo(created=days_ago(40), stars=5000), package(first_release=years_ago(6), last_30d=500_000))
assert with_package["age_days"] > 2000 and with_package["first_seen_from"] == "repository or package", "a verified package is part of the technology"
assert with_package["standing"] == "established", with_package
# stories and articles count only without any repository or package, and then traction needs three of them
old_talk = [talk("Hacker News", 2015, 1), talk("Hacker News", 2016, 2), talk("RSS feeds", 2018, 3)]
no_repo = record(*old_talk, own=False)
assert no_repo["standing"] == "established" and no_repo["first_seen_from"] == "stories and articles" and no_repo["traction"] == ["3 stories or articles about it"]
assert record(*old_talk[:2], own=False)["standing"] == "emerging", "two old stories: old, but no traction"
small_with_talk = record(repo(created=years_ago(8), stars=300), *old_talk)
assert small_with_talk["standing"] == "emerging" and small_with_talk["traction"] == [], "with a repository to ask, headlines do not count as traction"
not_own = record(repo(created=years_ago(9), stars=90_000), own=False)
assert not_own["own_repo"] is None and not_own["standing"] == "unknown", "a repository that only mentions the technology tells nothing about it"

# traction and scale from the package registries: downloads, image pulls, dependents
for meta, label in ((dict(last_30d=150_000), "npm foo: 150k downloads in 30 days"), (dict(lifetime=20_000_000), "npm foo: 20M pulls"),
                    (dict(dependent_packages=1500), "npm foo: 1.5k dependent packages")):
    pkg = record(package(first_release=years_ago(8), **meta), own=False)
    assert pkg["standing"] == "established" and pkg["traction"] == [label] and pkg["scale"] == [], (meta, pkg)
for meta in (dict(last_30d=2_000_000), dict(lifetime=300_000_000), dict(dependent_packages=15_000)):
    assert record(package(first_release=years_ago(8), **meta), own=False)["standing"] == "widespread", meta
assert record(package(first_release=years_ago(8), last_30d=5_000), own=False)["standing"] == "emerging", "downloads in the thousands: no traction"
assert record(package(registry="docker", package="library/x", official=True, lifetime=11_700_000_000, first_release="2014-01-01"), own=False)["standing"] == "widespread"
print("OK standing: new < emerging < established < widespread, tied to the technology, not to a name")

# ------------------------------------------------------------------ guards: only facts
assert guard("Adopt", widespread) == ("Adopt", []) and guard("Trial", widespread) == ("Trial", []) and guard("Hold", widespread) == ("Hold", [])
# nothing about age or size stops an ADOPT: the most known and used technologies must be able to be adopted, whatever a scan can see
for rec in (established, record(repo(created=years_ago(2), stars=50_000)), record(repo(created=years_ago(10), stars=200)), nothing):
    for ring in ("Adopt", "Trial", "Assess", "Hold"):
        assert guard(ring, rec) == (ring, []), (rec["standing"], ring)
for ring in ("Adopt", "Trial"):
    out, notes = guard(ring, new)
    assert out == "Assess" and notes == [f"{ring} → Assess: first seen only 40 days ago, nothing is proven yet"], notes
assert guard("Assess", new) == ("Assess", []) and guard("Hold", new) == ("Hold", []), "new technologies may still be Hold"
archived = record(repo(created=years_ago(10), stars=50_000, archived=True))
for ring in ("Adopt", "Trial", "Assess"):
    out, notes = guard(ring, archived)
    assert out == "Hold" and notes == [f"{ring} → Hold: the repository a/foo0 is archived"], notes
assert guard("Hold", archived) == ("Hold", []), "already Hold: nothing to note"
agpl = record(repo(created=years_ago(10), stars=50_000, license="AGPL-3.0"))
assert guard("Adopt", agpl) == ("Hold", ["Adopt → Hold: licensing_risk is 9 (license AGPL-3.0)"])
assert guard("Trial", record(repo(created=years_ago(10), stars=50_000, license="none")))[0] == "Trial", "no license is 7: not above 7"
assert guard("Adopt", record(repo(created=years_ago(10), stars=50_000, license="NOASSERTION")))[0] == "Adopt", "unclassified license is 5"
assert guard("Adopt", record(repo(created=years_ago(10), stars=50_000, license=None)))[0] == "Adopt", "an unknown license is not a risk to act on"
only_old_repo_archived = record(repo(0, created=years_ago(12), stars=20, archived=True), repo(1, created=years_ago(9), stars=40_000))
assert guard("Adopt", only_old_repo_archived)[0] == "Adopt", "an archived side repository does not archive the technology"
# a model's opinion is never a rule: only facts move a ring (the first rules vetoed Linux on the strength of headlines)
assert not any(hasattr(rr, name) for name in ("matrix_rules", "fatal_flaws", "ADOPT_MIN_MATURITY", "ASSESS_CAP_IF_ANY_RISK_AT_LEAST"))
print("OK guards: archived and license -> Hold, too new -> at most Assess; ADOPT is never capped; nothing else is a rule")

# pipeline.apply_rules is those guards plus the quadrant
c = candidate(repo(created=days_ago(40), stars=5000))
assert pipeline.apply_rules(c, {"ring": "Adopt", "quadrant": "Tools"})[0] == "Assess"
ring, quadrant, notes = pipeline.apply_rules(candidate(repo(created=years_ago(10), stars=50_000)), {"ring": "Adopt", "quadrant": "Platforms"})
assert (ring, quadrant, notes) == ("Adopt", "Platforms", [])
assert pipeline.apply_rules(candidate(repo(created=years_ago(10), stars=50_000)), {"ring": "nonsense", "quadrant": "nonsense"})[:2] == ("Assess", "Tools")
print("OK pipeline.apply_rules uses the guards")

# license risk: 0-10, risky licenses above 7
assert [rr.license_risk(x) for x in ("MIT", "Apache-2.0", "MPL-2.0", "GPL-3.0", "AGPL-3.0", "SSPL-1.0", "none", "NOASSERTION", "AGPL-1.0")]     == [1, 1, 3, 6, 9, 9, 7, 5, 9]
print("OK license risk")
print("ALL RECORD TESTS PASSED")
