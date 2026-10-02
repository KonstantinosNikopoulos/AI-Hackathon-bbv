"""Offline test of the gold-set harness (eval/run_gold.py, eval/gold_radar.json): no internet, no Ollama.
Run from the project folder:  python tests/test_gold.py"""
import importlib.util
import json
import os
import sys
import tempfile
import types
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "ollama" not in sys.modules:
    try:
        import ollama  # noqa: F401
    except ImportError:  # allow running the test without the package
        sys.modules["ollama"] = types.SimpleNamespace(Client=lambda **kw: None)

import config  # noqa: E402
from services import ai_service  # noqa: E402

spec = importlib.util.spec_from_file_location("run_gold", os.path.join(ROOT, "eval", "run_gold.py"))   # "eval" is not a package name
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)
spec = importlib.util.spec_from_file_location("variants", os.path.join(ROOT, "eval", "variants.py"))
variants = importlib.util.module_from_spec(spec)
spec.loader.exec_module(variants)

# ------------------------------------------------------------------ the gold file
items = rg.load_gold()
by_ring = {r: [i for i in items if i["ring"] == r] for r in config.RINGS}
assert all(len(v) >= 6 for v in by_ring.values()), {r: len(v) for r, v in by_ring.items()}   # every ring is tested by 6 or more
assert len({i["key"] for i in items}) == len(items) == len({i["name"] for i in items}), "unique names and keys"
BASES, CHECKS = {"lifecycle", "ubiquity", "formal", "consensus", "bbv"}, {"eol", "github_archived", "github_active", "cncf", "docker_pulls", "npm_downloads", "manual"}
for i in items:
    assert i["ring"] in config.RINGS and i["quadrant"] in config.QUADRANTS and i["basis"] in BASES, i["name"]
    assert i["fact"].endswith(".") and i["source"].startswith("https://") and i["what"] and i["check"]["type"] in CHECKS, i["name"]
    assert isinstance(i["packages"], dict) and set(i["packages"]) <= set(registry_names := {"npm", "pypi", "maven", "docker"}), i["name"]
    assert i["key"] == i["name"].lower(), "the key is the lower case name (no version stripping: 'Python 2' must not become 'python')"
assert all(i["basis"] == "lifecycle" for i in by_ring["Hold"]) and all(i["basis"] in ("formal",) for i in by_ring["Trial"] + by_ring["Assess"])
assert {i["quadrant"] for i in items} == set(config.QUADRANTS), "every quadrant is covered"
assert [i["name"] for i in rg.load_gold(rings=["Hold"], limit=2)] == ["Python 2", "AngularJS"]
assert len(rg.load_gold(limit=1)) == 4 and [i["name"] for i in rg.load_gold(names={"git"})] == ["Git"]
print("OK gold file: 4 rings, every quadrant, unique keys, a reason, a source and a check for each item")

# the held-out list: drawn by rules (eval/build_heldout.py), never the same technologies as the gold file, every ring, a check for each
held = rg.load_gold(os.path.join(ROOT, "eval", "heldout_radar.json"))
assert {i["ring"] for i in held} == set(config.RINGS) and len(held) >= 40, {r: sum(1 for i in held if i["ring"] == r) for r in config.RINGS}
assert not {i["key"] for i in held} & {i["key"] for i in items} and not {i["repo"].lower() for i in held if i["repo"]} & {i["repo"].lower() for i in items if i["repo"]}
assert len({i["key"] for i in held}) == len(held) and all(i["check"]["type"] in CHECKS and i["source"].startswith("https://") and i["fact"] for i in held)
assert all(i["quadrant"] in config.QUADRANTS and i["basis"] in BASES for i in held) and all(i["key"] == i["name"].lower() for i in held)
print("OK held-out file: other technologies than the gold file, every ring, a check for each item")

# ------------------------------------------------------------------ evidence collection with fake answers
assert rg.mentions("Python 2 is dead", ["Python 2"]) and not rg.mentions("Python 3 is here", ["Python 2"]) and rg.mentions("Postgres 17", ["PostgreSQL", "Postgres"])
NOW = date.today()
CALLS = []


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_get(url, params=None, headers=None, timeout=None):
    CALLS.append(url)
    if "api.github.com/repos/foo/bar" in url:
        return Resp({"name": "bar", "full_name": "foo/bar", "html_url": "https://github.com/foo/bar", "description": "A bar", "stargazers_count": 50000,
                     "language": "Go", "topics": [], "created_at": (NOW - timedelta(days=3000)).isoformat() + "T00:00:00Z", "pushed_at": NOW.isoformat() + "T00:00:00Z",
                     "forks_count": 10, "open_issues_count": 2, "license": {"spdx_id": "MIT"}, "archived": False})
    if "hn.algolia.com" in url:
        return Resp({"hits": [{"objectID": "1", "title": "Show HN: Bar 2.0", "url": "https://bar.dev/two", "points": 900, "num_comments": 90, "created_at": "2024-05-01T00:00:00Z"},
                              {"objectID": "2", "title": "Something about foo", "url": None, "points": 500, "num_comments": 5, "created_at": "2024-05-02T00:00:00Z"}]})
    if "wp-json" in url:
        if "devblogs.microsoft.com" in url:
            raise RuntimeError("HTTP 500")
        return Resp([{"title": {"rendered": "Why <b>Bar</b> won"}, "link": f"{url.split('/wp-json')[0]}/why-bar", "date": "2023-01-02T00:00:00", "excerpt": {"rendered": "<p>Bar is great.</p>"}},
                     {"title": {"rendered": "Unrelated post"}, "link": f"{url.split('/wp-json')[0]}/other", "date": "2023-01-03T00:00:00", "excerpt": {"rendered": "<p>It mentions Bar once.</p>"}}])
    return Resp({}, 404)


rg.requests.get = fake_get
ITEM = {"name": "Bar", "key": "bar", "ring": "Adopt", "quadrant": "Tools", "what": "A bar.", "basis": "ubiquity", "repo": "foo/bar", "packages": {}, "search": ["Bar"]}
tmp = tempfile.mkdtemp()
errors = []
press = rg.press_evidence(["Bar"], errors)
assert [p["title"] for p in press] == ["Why Bar won"] * 3 and {p["group"] for p in press} == {"thenewstack.io", "github.blog", "cncf.io"}, press
assert errors == ["press https://devblogs.microsoft.com/dotnet: HTTP 500"], "a site that fails is a warning; the title must be about the technology"
hn = rg.hn_evidence(["Bar"])
assert [h["title"] for h in hn] == ["Show HN: Bar 2.0"] and hn[0]["meta"] == {"points": 900, "comments": 90, "domain": "bar.dev"}

collected = rg.collect_evidence(ITEM, directory=tmp)
assert [e["source"] for e in collected["evidence"]] == ["GitHub", "Hacker News", "RSS feeds", "RSS feeds", "RSS feeds"], collected["evidence"]
assert collected["own_repos"] == ["https://github.com/foo/bar"] and len(collected["errors"]) == 1
assert os.listdir(tmp) == [], "an incomplete snapshot (a press site failed) is not saved, so no model is judged on it"
good = dict(ITEM, key="baz", search=["Bar"])
rg.PRESS_SITES[:] = [s for s in rg.PRESS_SITES if "devblogs" not in s]   # all remaining sites answer
first = rg.collect_evidence(good, directory=tmp)
calls = len(CALLS)
again = rg.collect_evidence(good, directory=tmp)
assert first["errors"] == [] and again == first and len(CALLS) == calls and os.listdir(tmp) == ["baz.json"], "saved once, then reused: same facts for every model"
candidate = rg.build_candidate(good, first)
assert candidate["mentions"] == 5 and candidate["sources"] == ["GitHub", "Hacker News", "RSS feeds"] and candidate["youngest_repo_days"] in (3000, 3001)
assert candidate["own_repos"] == ["https://github.com/foo/bar"] and rg.lanes_with_data(candidate) == ["github", "hackernews", "rss"]
with_registry = dict(first, evidence=first["evidence"] + [{"source": config.REGISTRY_SOURCE, "title": "npm: bar", "url": "u", "text": "t", "date": "", "meta": {}}])
c2 = rg.build_candidate(good, with_registry)
assert c2["mentions"] == 5 and c2["sources"] == candidate["sources"] and "packages" in rg.lanes_with_data(c2), "a registry lookup is not a mention"
print("OK evidence: GitHub, Hacker News and press by name, incomplete snapshots are not saved, saved ones are reused, registry data is no mention")

# ------------------------------------------------------------------ rating, report, comparison
class Structured:
    def __init__(self, schema, adopt):
        self.schema, self.adopt = schema, adopt

    async def ainvoke(self, messages):
        (_, system), (_, user) = messages
        title, tech = self.schema.get("title", "classify"), user.splitlines()[0].replace("Technology: ", "")
        if title.startswith("scorecard_"):
            return {"summary": "fine", "confidence": "high", **{n: 8 for n in self.schema["properties"] if n not in ("summary", "confidence")}}
        if title == "risk_memo":
            return {"biggest_risk": "Risk one.", "second_risk_or_unknown": "Risk two.", "what_must_be_true": "Risk three."}
        if title == "classify":   # an established technology is rated by the single prompt
            return {"quadrant": "Tools", "ring": "Adopt" if tech in self.adopt else "Hold", "summary": "s", "reason": "j", "confidence": "high",
                    "relevance": "HIGH", "business_value": "b"}
        return {"justification": "j", "category": "ADOPT" if tech in self.adopt else "HOLD", "confidence": "high", "relevance": "HIGH", "business_value": "b"}


class FakeChat:
    def __init__(self, adopt):
        self.adopt = adopt

    def with_structured_output(self, schema, method=None):
        return Structured(schema, self.adopt)


gold = [dict(ITEM, name="Bar", key="bar", ring="Adopt"), dict(ITEM, name="Old", key="old", ring="Hold", basis="lifecycle", repo=None),
        dict(ITEM, name="Mid", key="mid", ring="Trial", basis="formal", repo=None)]
cands = [rg.build_candidate(g, first if g["name"] == "Bar" else {"evidence": [], "own_repos": [], "errors": []}) for g in gold]
ratings = rg.rate_agents(cands, FakeChat(adopt={"Bar", "Mid"}), parallel=2)
outcomes = [rg.outcome(g, c, r) for g, c, r in zip(gold, cands, ratings)]
assert [(o["name"], o["predicted"]) for o in outcomes] == [("Bar", "Adopt"), ("Old", "Assess"), ("Mid", "Assess")], outcomes   # no data -> Assess
assert ratings[0]["route"] == "direct" and outcomes[0]["scored_lanes"] == {} and outcomes[0]["memo"] == "", "a widespread technology goes straight to the judge"
assert outcomes[1]["lanes"] == []
assert outcomes[0]["notes"] == [] and "Adopt" == outcomes[0]["llm_ring"]
outcomes.append(rg.outcome(gold[0], cands[0], RuntimeError("model down")))
assert outcomes[-1]["predicted"] is None and "model down" in outcomes[-1]["error"]
summary = rg.summarize(outcomes)
assert (summary["items"], summary["failed"], summary["exact"], summary["within_one"]) == (4, 1, 1, 3), summary
assert summary["by_ring"]["Adopt"] == [1, 2] and summary["matrix"]["Hold"]["Assess"] == 1 and summary["by_basis"]["lifecycle"] == [0, 1]
meta = {"model": "fake:1b", "host": "x", "mode": "agents", "seconds": 8.0}
text = rg.format_report(meta, outcomes, summary)
assert "exact ring: 1/4 = 25%" in text and "MISS Old" in text and "FAIL Bar" in text and "ok   Bar" in text and "within one ring: 3/4" in text
folder = os.path.join(tmp, "results")
stem = rg.save_result(meta, outcomes, summary, folder)
assert sorted(os.listdir(folder)) == [os.path.basename(stem) + ".json", os.path.basename(stem) + ".txt"]
rg.save_result(dict(meta, model="other:2b", mode="classic", seconds=4.0), outcomes[:3], rg.summarize(outcomes[:3]), folder)
table = rg.compare(folder)
assert "fake:1b/age" in table and "other:2b/cla" in table and "Assess x" in table and "exact ring" in table and "seconds per technology" in table, table
assert rg.compare(os.path.join(tmp, "empty")).startswith("no saved results")
rg.save_result(dict(meta, gold="heldout", rules="v2", seconds=6.0), outcomes[:3], rg.summarize(outcomes[:3]), folder)
assert "fake:1b/age/v2" in rg.compare(folder), "a labelled run is a column of its own"
only = rg.compare(folder, gold="heldout")
assert "fake:1b/age/v2" in only and "other:2b" not in only, "compare(gold=...) keeps only the results for that list"
assert "heldout-fake-1b-agents-v2-" in " ".join(os.listdir(folder)), os.listdir(folder)
llm = types.SimpleNamespace(chat_json=lambda system, user, schema: {"quadrant": "Tools", "ring": "Adopt", "summary": "s", "reason": "r", "business_value": "b",
                                                                  "relevance": "HIGH", "confidence": "high"})
classic = rg.rate_classic(cands[:1], llm)
assert rg.outcome(gold[0], cands[0], classic[0])["predicted"] == "Adopt"
print("OK rating: agents and classic mode, failures counted, report, saved results and the side by side comparison")

# rescoring: the SAME model answers, the CURRENT rules (a change of the rules is measured without asking the model again)
small = {"evidence": [{"source": "GitHub", "title": "foo/small", "url": "https://github.com/foo/small", "text": "t", "date": "2016-01-01",
                       "meta": {"name": "small", "full_name": "foo/small", "stars": 300, "created_at": "2016-01-01", "pushed_at": NOW.isoformat(),
                                "license": "MIT", "archived": False, "forks": 1, "open_issues": 1}}], "own_repos": ["https://github.com/foo/small"], "errors": []}
json.dump(small, open(os.path.join(tmp, "small.json"), "w", encoding="utf-8"))
small_item = dict(ITEM, name="Small", key="small", ring="Trial", basis="formal", repo=None)
small_rating = rg.rate_agents([rg.build_candidate(small_item, small)], FakeChat(adopt={"Small"}), parallel=1)
small_outcome = rg.outcome(small_item, rg.build_candidate(small_item, small), small_rating[0])
assert small_outcome["llm_ring"] == "Adopt" and small_outcome["predicted"] == "Trial" and small_outcome["memo"] == "Risk one. Risk two. Risk three."
assert "ADOPT needs years of use at scale" in small_outcome["notes"][0], "a small technology gets the skeptic, and its ADOPT is capped by the guards"
saved = {"meta": {"model": "m", "mode": "classic"},
         "outcomes": [{"name": "Small", "expected": "Trial", "basis": "formal", "lanes": [], "predicted": "Adopt", "llm_ring": "Adopt"},
                      {"name": "Bar", "expected": "Adopt", "basis": "ubiquity", "lanes": [], "predicted": "Adopt", "llm_ring": "Adopt"},
                      {"name": "Down", "expected": "Hold", "basis": "lifecycle", "lanes": [], "predicted": None, "error": "RuntimeError: x"}]}
again = rg.rescore(saved, [small_item, dict(good, name="Bar"), dict(ITEM, name="Down", key="down")], directory=tmp)
assert [(o["name"], o["predicted"]) for o in again] == [("Small", "Trial"), ("Bar", "Adopt"), ("Down", None)], again
assert again[0]["llm_ring"] == "Adopt" and "ADOPT needs years of use at scale" in again[0]["notes"][0] and again[1]["notes"] == []
assert again[2] == saved["outcomes"][2], "a failed call stays failed"

# the variants: the table that combines three small questions, and what each variant reads
V = variants
assert V.combine("retired", "widespread", "stable") == "Hold" and V.combine("active", "widespread", "stable") == "Adopt"
assert V.combine("active", "widespread", "young") == "Trial" and V.combine("active", "established", "stable") == "Trial"
assert V.combine("active", "niche", "stable") == "Assess" and V.combine("active", "unproven", "stable") == "Assess"
assert V.combine("active", "widespread", "experimental") == "Assess" and V.combine("active", "established", "experimental") == "Assess"
assert set(V.QUESTIONS) == {"lifecycle", "adoption", "maturity"} and all(V.question_schema(n, o)["properties"]["answer"]["enum"] == o for n, (_, o) in V.QUESTIONS.items())
assert set(V.RING_OF_ADOPTION) == set(V.QUESTIONS["adoption"][1]) and set(V.RING_CAP_OF_MATURITY) == set(V.QUESTIONS["maturity"][1])
seen = []


class VariantChat:
    def __init__(self, lifecycle="active"):
        self.lifecycle = lifecycle

    def with_structured_output(self, schema, method=None):
        title, lifecycle = schema.get("title", "classify"), self.lifecycle

        class Runnable:
            async def ainvoke(self, messages):
                (_, system), (_, user) = messages
                seen.append((title, system, user))
                if title == "verdict":
                    return {"justification": "j", "category": "TRIAL", "confidence": "high", "relevance": "HIGH", "business_value": "b"}
                if title == "classify":
                    return {"quadrant": "Tools", "ring": "Adopt", "summary": "s", "reason": "r", "business_value": "b", "relevance": "HIGH", "confidence": "high"}
                return {"reason": "r", "answer": {"lifecycle": lifecycle, "adoption": "widespread", "maturity": "stable"}[title.removeprefix("question_")]}

        return Runnable()


one = cands[:1]
classic_facts = V.rate_all(one, VariantChat(), "classicfacts")[0]
assert classic_facts["answer"]["ring"] == "Adopt"
title, system, user = seen[-1]
assert system == ai_service.classify_system() and user.startswith(ai_service.classify_message(cands[0])) and "Facts measured by code" in user
seen.clear()
asked = V.rate_all(one, VariantChat(), "questions")[0]
assert asked["answer"]["ring"] == "Adopt" and asked["answer"]["reason"] == "lifecycle active; adoption widespread; maturity stable"
assert sorted(t for t, _, _ in seen) == ["question_adoption", "question_lifecycle", "question_maturity"] and len({u for _, _, u in seen}) == 1, \
    "three small questions, each reading the same whole picture"
assert V.rate_all(one, VariantChat("retired"), "questions")[0]["answer"]["ring"] == "Hold"


class Broken(VariantChat):
    def with_structured_output(self, schema, method=None):
        runnable = super().with_structured_output(schema, method)

        class Wrong:
            async def ainvoke(self, messages):
                return {"reason": "r", "answer": "maybe"}
        return Wrong() if schema["title"] == "question_adoption" else runnable


assert isinstance(V.rate_all(one, Broken(), "questions")[0], ValueError), "an invalid answer is an error, never a guessed ring"
fresh = rg.outcome(gold[0], cands[0], V.rate_all(one, VariantChat(), "questions")[0])
assert fresh["predicted"] == "Adopt" and fresh["reason"].startswith("lifecycle active")
print("OK rescoring with the current rules, and the variants: the table of three questions, what each variant reads, invalid answers fail")

# ------------------------------------------------------------------ verifying the labels against live sources (faked here)
def verify_get(url, params=None, headers=None, timeout=None):
    if "endoflife.date/api/python" in url:
        return Resp([{"cycle": "3.13", "eol": "2029-10-31"}, {"cycle": "2.7", "eol": "2020-01-01"}])
    if "endoflife.date/api/nope" in url:
        return Resp([{"cycle": "1", "eol": False}])
    if "repos/old/archived" in url:
        return Resp({"archived": True})
    if "repos/hot/repo" in url:
        return Resp({"archived": False, "stargazers_count": 90000, "pushed_at": (NOW - timedelta(days=2)).isoformat() + "T00:00:00Z"})
    if "hub.docker.com" in url:
        return Resp({"pull_count": 5_000_000_000})
    if "api.npmjs.org" in url:
        return Resp({"downloads": 10})
    if "landscape.yml" in url:
        return types.SimpleNamespace(text="categories:\n  - subcategories:\n      - items:\n          - {name: Alpha, project: graduated}\n          - {name: Beta, project: sandbox}\n")
    return Resp({}, 404)


rg.requests.get = verify_get
checks = [
    {"name": "A", "source": "s", "check": {"type": "eol", "product": "python", "cycle": "2.7"}},
    {"name": "B", "source": "s", "check": {"type": "eol", "product": "nope", "cycle": "1"}},
    {"name": "C", "source": "s", "check": {"type": "github_archived", "repo": "old/archived"}},
    {"name": "D", "source": "s", "check": {"type": "github_active", "repo": "hot/repo", "min_stars": 50000, "max_days_since_push": 30}},
    {"name": "E", "source": "s", "check": {"type": "docker_pulls", "image": "library/x", "min": 1_000_000_000}},
    {"name": "F", "source": "s", "check": {"type": "npm_downloads", "package": "p", "min": 1000}},
    {"name": "G", "source": "s", "check": {"type": "cncf", "name": "Alpha", "level": "graduated"}},
    {"name": "H", "source": "s", "check": {"type": "cncf", "name": "Beta", "level": "incubating"}},
    {"name": "I", "source": "s", "check": {"type": "manual"}},
    {"name": "J", "source": "s", "check": {"type": "docker_pulls", "image": "missing"}},
]
got = {name: status for name, status, _ in rg.verify_labels(checks)}
assert got == {"A": "PASS", "B": "FAIL", "C": "PASS", "D": "PASS", "E": "PASS", "F": "FAIL", "G": "PASS", "H": "FAIL", "I": "manual", "J": "error"}, got
print("OK label verification: every check type passes, fails or reports an error, never crashes")

# ------------------------------------------------------------------ the thinking switch is asked of Ollama, with a name based fallback
class Show:
    def __init__(self, caps):
        self.capabilities = caps


def client_for(models):
    class Client:
        def __init__(self, **kw):
            pass

        def show(self, model):
            if model not in models:
                raise RuntimeError("model not found")
            return Show(models[model])
    return Client


ai_service.ollama = types.SimpleNamespace(Client=client_for({"gemma4:e4b": ["completion", "thinking"], "llama3.2:3b": ["completion", "tools"],
                                                             "gpt-oss:20b": ["completion", "thinking"]}))
ai_service._THINKING.clear()
assert ai_service.thinking_setting("h", "gemma4:e4b") is False, "a thinking model that is not on the old list is switched off too"
assert ai_service.thinking_setting("h", "llama3.2:3b") is None and ai_service.thinking_setting("h", "gpt-oss:20b") == "low"
assert ai_service.thinking_setting("h", "qwen3:4b") is False and ai_service.thinking_setting("h", "mystery:1b") is None, "unknown model: the name guess"
print("OK thinking switch: capability based, gpt-oss gets 'low', fallback by name")
print("ALL GOLD TESTS PASSED")
