"""Offline test of the facts the rating prompt reads (services/radar_record.py): no internet, no Ollama.
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

from services import ai_service, pipeline, radar_record as rr  # noqa: E402

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


def candidate(*evidence, own=True):
    github = [e for e in evidence if e["source"] == "GitHub"]
    return {"name": "Foo", "key": "foo", "what": "A foo.", "quadrant": "Tools", "mentions": len(evidence), "sources": ["GitHub"], "signal": 1,
            "youngest_repo_days": None, "own_repos": [e["url"] for e in github] if own else [], "evidence": list(evidence)}


# ------------------------------------------------------------------ license risk: 0-10
assert [rr.license_risk(x) for x in ("MIT", "Apache-2.0", "MPL-2.0", "GPL-3.0", "AGPL-3.0", "SSPL-1.0", "none", "NOASSERTION", "AGPL-1.0")] \
    == [1, 1, 3, 6, 9, 9, 7, 5, 9]
print("OK license risk")

# ------------------------------------------------------------------ the main own repository
own = rr.own_repo(candidate(repo(0, created=years_ago(10), stars=50_000)), TODAY)
assert own["repo"] == "a/foo0" and own["stars"] == 50_000 and own["archived"] is False and own["license"] == "MIT" and own["licensing_risk"] == 1
assert own["age_days"] > 3000 and own["days_since_push"] == 2
big = rr.own_repo(candidate(repo(0, created=years_ago(12), stars=20), repo(1, created=days_ago(40), stars=9000)), TODAY)
assert big["repo"] == "a/foo1" and big["age_days"] == 40, "the most starred repository speaks for the technology"
assert rr.own_repo(candidate(repo(), own=False), TODAY) is None, "a repository that only mentions the technology is not its own"
assert rr.own_repo({"name": "X"}, TODAY) is None, "a candidate without evidence does not crash"
assert rr.own_repo(candidate(repo(license=None)), TODAY)["licensing_risk"] is None, "an unknown license is not a risk"
print("OK the main own repository")

# ------------------------------------------------------------------ the facts as the lines the prompt reads
facts = rr.format_facts(candidate(repo(created="2016-10-02", stars=50_000, pushed=days_ago(2))), TODAY)
assert facts == ("- Its own GitHub repository a/foo0: 50k stars, created 2016-10-02 (3,652 days ago), last push 2 days ago, "
                 "license MIT, not archived."), facts
archived = rr.format_facts(candidate(repo(archived=True)), TODAY)
assert "ARCHIVED (read-only: nobody maintains it)." in archived and "not archived" not in archived
agpl = rr.format_facts(candidate(repo(license="AGPL-3.0")), TODAY)
assert "license AGPL-3.0 (HIGH licensing risk: blocks commercial reuse)" in agpl
for fine in ("MIT", "GPL-3.0", "none", "NOASSERTION"):
    assert "HIGH" not in rr.format_facts(candidate(repo(license=fine)), TODAY), fine
assert "(40 days ago)" in rr.format_facts(candidate(repo(created=days_ago(40))), TODAY)
assert rr.format_facts(candidate(repo(), own=False), TODAY) == \
    "- No GitHub repository of its own was found for it (this says nothing about how old or how used it is)."
print("OK the facts as text: age, stars, last push, license, archived")

# ------------------------------------------------------------------ the rules are in the prompt, and no code changes a ring
prompt = ai_service.classify_system()
for rule in ("ARCHIVED", "HIGH licensing risk", "last 6 months", "cannot be Adopt or Trial", "Adopt: a technology you know to be a standard",
             "Package registries"):
    assert rule in prompt, rule
assert not any(hasattr(rr, name) for name in ("apply_guards", "build_record", "matrix_rules", "fatal_flaws"))
for ring in ("Adopt", "Trial", "Assess", "Hold"):   # pipeline.apply_rules only validates: a ring is never changed
    assert pipeline.apply_rules(candidate(repo(created=days_ago(40), archived=True)), {"ring": ring, "quadrant": "Platforms"}) == (ring, "Platforms", [])
assert pipeline.apply_rules(candidate(repo()), {"ring": "nonsense", "quadrant": "nonsense"}) == ("Assess", "Tools", [])
message = ai_service.classify_message(candidate(repo(license="AGPL-3.0", archived=True)))
assert message.index("Evidence:") < message.index("Facts measured by code (true):") and "HIGH licensing risk" in message and "ARCHIVED" in message
print("OK the rules are in the prompt; code only validates the ring")
print("ALL RECORD TESTS PASSED")
