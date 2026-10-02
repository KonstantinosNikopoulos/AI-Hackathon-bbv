"""Offline test of the whole pipeline: no internet, no Ollama.
Run from the project folder:  python tests/test_offline.py"""
import os
import re
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "ollama" not in sys.modules:
    try:
        import ollama  # noqa: F401
    except ImportError:  # allow running the test without the package
        sys.modules["ollama"] = types.SimpleNamespace(Client=lambda **kw: None)

import config  # noqa: E402

config.DATA_DIR = tempfile.mkdtemp()
from services import ai_service, export, pipeline, radar_chart, rss_service, storage  # noqa: E402
from services import github_service, hn_service, registry_service, yc_service  # noqa: E402

pipeline.CACHE_DIR = os.path.join(config.DATA_DIR, "cache")
registry_service.PYPISTATS_GAP = 0   # no real pause between pypistats requests
# One technology is curated (OpenTelemetry on npm and Maven); "ty" is found through its own GitHub repository; the rest has no package.
pipeline.PACKAGE_MAP = {"opentelemetry": {"npm": ["@opentelemetry/api"], "maven": ["io.opentelemetry:opentelemetry-api"]}}

now = datetime.now(timezone.utc)
ago = lambda d: now - timedelta(days=d)  # noqa: E731

RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>InfoQ</title>
<item><title><![CDATA[OpenTelemetry Profiling Reaches Beta]]></title><link>https://www.infoq.com/news/otel/?utm=1</link>
<pubDate>{format_datetime(ago(2))}</pubDate><description>&lt;p&gt;OpenTelemetry adds profiling.&lt;/p&gt;</description></item>
<item><title>Startup raises $40M for observability</title><link>https://www.infoq.com/news/money</link>
<pubDate>{format_datetime(ago(1))}</pubDate><description>money</description></item>
<item><title>Kubernetes 1.40 adds in-place pod resize</title><link>https://www.infoq.com/news/k8s/</link>
<pubDate>{format_datetime(ago(4))}</pubDate><description>Kubernetes release.</description></item>
<item><title>Old news</title><link>https://www.infoq.com/news/old</link><pubDate>{format_datetime(ago(90))}</pubDate></item>
</channel></rss>"""
ATOM = f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Fowler</title>
<entry><title type="html">Using the Model Context Protocol in enterprises</title>
<link rel="alternate" href="https://martinfowler.com/articles/mcp.html"/><updated>{ago(3).isoformat()}</updated>
<summary>How MCP connects agents to tools.</summary></entry>
<entry><title>Blazor United unifies server and client rendering</title>
<link rel="alternate" href="https://martinfowler.com/articles/blazor-united.html"/><updated>{ago(5).isoformat()}</updated>
<summary>Blazor now renders on the server and the client.</summary></entry>
<entry><title>Blazor WebAssembly gets ahead-of-time compilation</title>
<link rel="alternate" href="https://martinfowler.com/articles/blazor-aot.html"/><updated>{ago(6).isoformat()}</updated>
<summary>Blazor apps start faster with AOT.</summary></entry></feed>"""


class Resp:
    def __init__(self, data=None, content=b"", status=200):
        self._data, self.content, self.status_code, self.text = data, content, status, ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


REGISTRY_CALLS = []


def registry_get(url):
    """npm, pypistats, ecosyste.ms and Docker Hub with a few packages: PyPI "ty" (its repository is astral-sh/ty), npm
    @opentelemetry/api and its Maven artifact. An npm package called "ty" belongs to somebody else. Everything else: 404."""
    REGISTRY_CALLS.append(url)

    def eco(**changes):
        return Resp({"latest_release_number": "1.0.0", "latest_release_published_at": "2026-09-01T00:00:00.000Z", "versions_count": 12,
                     "dependent_packages_count": 40, "dependent_repos_count": 900, "downloads": 1_000_000,
                     "downloads_period": "last-month", **changes})

    if "registries/pypi.org/packages/ty" in url:
        return eco(repository_url="git+https://github.com/astral-sh/ty.git")
    if "registries/npmjs.org/packages/ty" in url:
        return eco(repository_url="https://github.com/someone/ty")
    if "registries/npmjs.org/packages/%40opentelemetry%2Fapi" in url:
        return eco(repository_url="https://github.com/open-telemetry/opentelemetry-js")
    if "registries/repo1.maven.org/packages/io.opentelemetry%3Aopentelemetry-api" in url:
        return eco(repository_url="https://github.com/open-telemetry/opentelemetry-java", downloads=None, downloads_period=None)
    if "pypistats.org/api/packages/ty/overall" in url:   # 30000, 60000 ... 180000 downloads in the six months
        end = now.date() - timedelta(days=1)
        return Resp({"data": [{"category": "without_mirrors", "date": str(end - timedelta(days=age)),
                               "downloads": 1000 * (6 - age // 30)} for age in range(179, -1, -1)]})
    if "api.npmjs.org/downloads/range/" in url and "@opentelemetry/api" in url:   # 3M, 6M ... 36M downloads in the twelve months
        start, end = (datetime.fromisoformat(x).date() for x in re.search(r"range/([\d-]+):([\d-]+)/", url).groups())
        n = (end - start).days + 1
        return Resp({"downloads": [{"day": str(start + timedelta(days=i)), "downloads": 100_000 * (12 - (n - 1 - i) // 30)}
                                   for i in range(n)]})
    return Resp(status=404)


def fake_get(url, params=None, headers=None, timeout=None):
    if any(host in url for host in ("ecosyste.ms", "pypistats.org", "api.npmjs.org", "hub.docker.com")):
        return registry_get(url)
    if "api.github.com" in url:
        return Resp({"items": [
            {"name": "ty", "full_name": "astral-sh/ty", "language": "Rust", "html_url": "https://github.com/astral-sh/ty",
             "description": "An extremely fast Python type checker", "stargazers_count": 4200,
             "created_at": ago(40).isoformat(), "topics": ["python", "type-checker"], "license": {"spdx_id": "MIT"},
             "pushed_at": ago(1).isoformat(), "forks_count": 90, "open_issues_count": 120, "archived": False},
            {"name": "otel-thing", "full_name": "foo/otel-thing", "language": "Go", "html_url": "https://github.com/foo/otel-thing",
             "description": "OpenTelemetry collector plugin", "stargazers_count": 300, "created_at": ago(20).isoformat(), "topics": []},
        ]})
    if "yc-oss" in url:
        return Resp([
            {"name": "OldCo", "status": "Active", "batch": "Winter 2012", "one_liner": "AI for spreadsheets", "slug": "oldco", "tags": ["AI"]},
            {"name": "NewCo", "status": "Active", "batch": "Summer 2026", "one_liner": "MCP gateway for AI agents", "slug": "newco", "tags": ["AI", "Developer Tools"]},
            {"name": "DeadCo", "status": "Inactive", "batch": "Summer 2026", "one_liner": "AI", "slug": "dead", "tags": []},
        ])
    if "algolia" in url:
        return Resp({"hits": [
            {"title": "Show HN: A Model Context Protocol server for Postgres", "url": "https://example.com/mcp-pg",
             "points": 420, "num_comments": 120, "created_at": ago(1).isoformat(), "objectID": "1"},
            {"title": "Ask HN: Who is hiring? (October 2026)", "url": None, "points": 900, "num_comments": 1000,
             "created_at": ago(1).isoformat(), "objectID": "2"},
            {"title": "OpenTelemetry Profiling Reaches Beta", "url": "https://www.infoq.com/news/otel/", "points": 200,
             "num_comments": 40, "created_at": ago(2).isoformat(), "objectID": "3"},
        ]})
    if "infoq" in url:
        return Resp(content=RSS.encode())
    if "martinfowler" in url:
        return Resp(content=ATOM.encode())
    return Resp(status=404)


for module in (github_service, yc_service, hn_service, rss_service, registry_service):
    module.requests.get = fake_get


class FakeLLM:
    """Pretends to be the model: finds technologies by keyword and gives rings."""
    model = "fake"

    def __init__(self):
        self.calls = 0
        self.batches = []
        self.rated = []   # (technology, system prompt, user message) of every rating call

    def chat_json(self, system, user, schema):
        self.calls += 1
        if user.startswith("Items from"):
            self.batches.append((user.splitlines()[0], system.splitlines()[0], user))
            techs = []
            for name, quadrant, pattern in [("OpenTelemetry", "Tools", "opentelemetry"),
                                            ("MCP", "Techniques", "model context protocol"),
                                            ("ty", "Tools", "type checker"), ("Kubernetes", "Platforms", "kubernetes"),
                                            ("Blazor", "Languages & Frameworks", "blazor"), ("AI", "Techniques", "")]:
                ids = [int(line[1:line.index("]")]) for line in user.splitlines()[1:] if pattern in line.lower()]
                if ids:
                    techs.append({"name": name, "quadrant": quadrant, "what": f"{name} is a thing", "items": ids})
            return {"technologies": techs}
        assert user.startswith("Technology: ") and "Evidence:" in user, user   # rating: the single prompt
        tech = user.splitlines()[0].replace("Technology: ", "")
        self.rated.append((tech, system, user))
        ring = "Adopt" if tech in ("ty", "OpenTelemetry", "MCP", "Blazor") else "Hold" if tech == "Kubernetes" else "Assess"
        return {"quadrant": "Tools", "ring": ring, "summary": f"{tech} is a thing", "reason": "Because of the evidence.",
                "business_value": "IoT & medtech", "relevance": "HIGH", "confidence": "medium"}


settings = {"area": "All", "sources": config.SOURCES, "days": 30, "max_signals": 40, "top_n": 10,
            "model": "fake", "host": "x"}
config.RSS_FEEDS[:] = ["https://feed.infoq.com/", "https://martinfowler.com/feed.atom", "https://broken.example/feed"]
pipeline.RSS_FEEDS = config.RSS_FEEDS
llm = FakeLLM()
msgs = []
result = pipeline.run_scan(settings, llm, lambda m, f: msgs.append((round(f, 2), m)))

titles = [s["title"] for s in result["signals"]]
print("SIGNALS:", *[f"  {s['n']:>2} [{s['source']}] {s['title']}" for s in result["signals"]], sep="\n")
assert not any("raises" in t or "hiring" in t.lower() or "Old news" in t for t in titles), "noise not removed"
assert titles.count("OpenTelemetry Profiling Reaches Beta") == 1, "duplicate not removed"
rss_items = rss_service.get_rss_items(["https://feed.infoq.com/"], days=30)
assert rss_items[0]["text"] == "OpenTelemetry adds profiling." and len(rss_items) == 3, f"RSS parse/clean: {rss_items}"
assert rss_items[0]["url"].startswith("https://www.infoq.com/news/otel/")
assert any("broken.example" in e for e in result["errors"]), "broken feed should be a warning"

techs = {t["name"]: t for t in result["technologies"]}
print("TECHNOLOGIES:", *[f"  {t['name']}: {t['ring']} ({t['quadrant']}) mentions={t['mentions']} notes={t['rule_notes']}"
                         for t in result["technologies"]], sep="\n")
assert "AI" not in techs, "generic word should be dropped"
for name in ("ty", "OpenTelemetry", "MCP", "Blazor"):   # the rules are in the prompt: no code changes the model's ring (the fake model says Adopt)
    assert techs[name]["ring"] == "Adopt" and techs[name]["llm_ring"] == "Adopt" and techs[name]["rule_notes"] == [], name
assert techs["Kubernetes"]["ring"] == "Hold" and techs["Kubernetes"]["mentions"] == 1, "model ring used as is"
assert set(techs) == {"OpenTelemetry", "MCP", "ty", "Kubernetes", "Blazor"}, "only technologies found in the signals"

# one prompt for the whole app: every batch holds one source, names it in the user message, and gets the [EXTRACTION] section
print("BATCHES:", *[f"  {head} -> system starts: {sys_line[:60]}" for head, sys_line, _ in llm.batches], sep="\n")
assert {h.replace("Items from ", "").rstrip(":") for h, _, _ in llm.batches} == {"GitHub", "Y Combinator", "Hacker News", "RSS feeds"}
assert all(sys_line == ai_service.extraction_system().splitlines()[0] for _, sys_line, _ in llm.batches), "the same prompt for every source"
gh_batch = next(u for h, _, u in llm.batches if "GitHub" in h)
assert "language: Rust" in gh_batch and "stars" in gh_batch, gh_batch
hn_batch = next(u for h, _, u in llm.batches if "Hacker News" in h)
assert "points" in hn_batch and "link: example.com" in hn_batch, hn_batch
assert set(result["settings"]["prompts"]) == {"prompt"}

# rating: ONE call per technology with the single prompt, which reads every evidence line (registry numbers included)
assert [t for t, _, _ in llm.rated].count("ty") == 1 and sorted(t for t, _, _ in llm.rated) == sorted(techs), llm.rated
assert all(system == ai_service.classify_system() for _, system, _ in llm.rated), "the [RATING] section of prompts/prompt.md"
ty_user = next(u for t, _, u in llm.rated if t == "ty")
assert "Package registries: PyPI: ty - 180,000 downloads in the last 30 days" in ty_user and "astral-sh/ty" in ty_user, ty_user
assert not any("scorecards" in t or "risk_memo" in t for t in result["technologies"]), "no agents: one prompt holds the rules"
# the facts the rules need are in the message: the age of the technology's own repository (40 days for ty), after the evidence
assert ty_user.index("Evidence:") < ty_user.index("Facts measured by code (true):") and "astral-sh/ty: 4.2k stars" in ty_user and "(40 days ago)" in ty_user, ty_user
kubernetes_user = next(u for t, _, u in llm.rated if t == "Kubernetes")
assert "No GitHub repository of its own was found for it" in kubernetes_user, kubernetes_user

# package registries, the fifth source: looked up per technology, only what can be tied to it, and never an extra "mention"
registry = lambda t: [e for e in t["evidence"] if e["source"] == config.REGISTRY_SOURCE]  # noqa: E731
ty_packages = [e["meta"] for e in registry(techs["ty"])]
assert [(p["registry"], p["package"], p["match"]) for p in ty_packages] == [("pypi", "ty", "verified")], \
    "PyPI 'ty' links to the astral-sh/ty repository; the npm 'ty' belongs to someone else and is not counted"
assert ty_packages[0]["monthly"] == [30_000, 60_000, 90_000, 120_000, 150_000, 180_000]
otel_packages = [e["meta"] for e in registry(techs["OpenTelemetry"])]
assert [(p["registry"], p["match"]) for p in otel_packages] == [("npm", "curated"), ("maven", "curated")]
assert otel_packages[0]["monthly"] == [3_000_000 * k for k in range(1, 13)]
assert techs["ty"]["mentions"] == 1 and techs["ty"]["sources"] == ["GitHub"], "a download count is not a mention"
assert [e["source"] for e in techs["ty"]["evidence"]] == ["GitHub", "Package registries"], "the lookup is shown as evidence"
assert techs["ty"]["evidence"][1]["url"] == "https://pypi.org/project/ty/"
assert sorted(n for n, t in techs.items() if registry(t)) == ["OpenTelemetry", "ty"], "no package, no registry evidence"
assert [u for u in REGISTRY_CALLS if "kubernetes" in u] == ["https://hub.docker.com/v2/repositories/library/kubernetes/"], \
    "without an own repository or a curated entry a name is only looked up as a Docker official image"
assert len(REGISTRY_CALLS) == 11, REGISTRY_CALLS   # OpenTelemetry 3, ty 5, and 1 each for MCP, Kubernetes and Blazor
print("RATING CALLS:", len(llm.rated), "| extraction calls:", llm.calls - len(llm.rated), "| registry requests:", len(REGISTRY_CALLS))

# source cache: a second scan with the network down reuses the fetched data, the registry lookups included
for module in (github_service, yc_service, hn_service, rss_service, registry_service):
    module.requests.get = lambda *a, **k: (_ for _ in ()).throw(Exception("offline"))
again = pipeline.run_scan(settings, FakeLLM())
assert len(again["signals"]) == len(result["signals"]) and not again["errors"][1:], again["errors"]
found = lambda r: {t["name"]: [e["source"] for e in t["evidence"]] for t in r["technologies"]}  # noqa: E731
assert found(again) == found(result) and not any("Package registries" in e for e in again["errors"]), "registry lookups are cached too"
# without the fifth source nothing is looked up (the network is down, so a lookup would show up as a warning)
without = pipeline.run_scan(dict(settings, sources=[s for s in config.SOURCES if s != config.REGISTRY_SOURCE]), FakeLLM())
assert not any(e["source"] == config.REGISTRY_SOURCE for t in without["technologies"] for e in t["evidence"])
assert not any("Package registries" in e for e in without["errors"])
for module in (github_service, yc_service, hn_service, rss_service, registry_service):
    module.requests.get = fake_get
assert msgs[-1][0] == 1.0

# look-back: any whole number of days works, however large (the UI stopped at 90; dates overflow beyond ~740000 days)
assert [config.clamp_days(d) for d in (0, -5, 1, "45", 3650, 36500, 10**12, 7.9)] == [1, 1, 1, 45, 3650, 36500, 36500, 7]
seen, real_get = [], github_service.requests.get   # the one requests module all services share


def recording_get(url, params=None, headers=None, timeout=None):
    seen.append((url, params))
    return fake_get(url, params, headers, timeout)


github_service.requests.get = recording_get
for days in (1, 5, 89, 91, 3650, 10**6, 10**12):
    seen.clear()
    github_service.get_trending_repositories(limit=3, technology_area="All", days=days)
    hn_service.get_hn_stories(limit=3, days=days)
    old_enough = len(rss_service.get_rss_items(["https://feed.infoq.com/"], days=days))
    (github_url, github_params), (hn_url, hn_params) = seen[0], seen[1]
    since = datetime.strptime(re.search(r"created:>([\d-]+)", github_params["q"]).group(1), "%Y-%m-%d")
    assert abs((datetime.now() - since).days - config.clamp_days(days)) <= 1, (days, github_params["q"])
    assert hn_url == "https://hn.algolia.com/api/v1/search", "points-ranked: the most popular stories of the WHOLE window"
    since_hn = int(re.search(r"created_at_i>(-?\d+)", hn_params["numericFilters"]).group(1))
    assert abs(time.time() - since_hn - config.clamp_days(days) * 86400) < 5, (days, hn_params)
    assert old_enough == sum(1 for age in (2, 1, 4, 90) if age < days), (days, old_enough)   # ages of the RSS fixture's articles
github_service.requests.get = real_get
huge = pipeline.run_scan(dict(settings, days=10**9), FakeLLM())   # a whole scan with an absurd look-back
assert huge["settings"]["days"] == 10**9 and len(huge["signals"]) >= len(result["signals"]) and huge["technologies"]
print("OK look-back: 1 to 10^12 days, GitHub / Hacker News / RSS windows, points-ranked Hacker News search")

# nothing is capped: top_n=None rates every technology found, and more signals make every source fetch more
assert len(pipeline.run_scan(dict(settings, top_n=3), FakeLLM())["technologies"]) == 3
everything = pipeline.run_scan(dict(settings, top_n=None), FakeLLM())
assert len(everything["technologies"]) == everything["stats"]["candidates"] == 5, "every technology found is rated"
assert len(pipeline.run_scan({k: v for k, v in settings.items() if k != "top_n"}, FakeLLM())["technologies"]) == 5, "no top_n setting: all"
limits = []
real_fetchers = (github_service.get_trending_repositories, yc_service.get_yc_companies, hn_service.get_hn_stories, rss_service.get_rss_items)
github_service.get_trending_repositories = lambda limit, **kw: limits.append(("github", limit)) or []
yc_service.get_yc_companies = lambda limit, **kw: limits.append(("yc", limit)) or []
hn_service.get_hn_stories = lambda limit, **kw: limits.append(("hn", limit)) or []
rss_service.get_rss_items = lambda feeds, per_feed, **kw: limits.append(("rss", per_feed)) or []
for wanted, expected in ((40, [("github", 15), ("yc", 10), ("hn", 15), ("rss", 4)]),
                         (400, [("github", 100), ("yc", 100), ("hn", 100), ("rss", 34)]),   # 400 / 4 sources = 100 each; 3 feeds in this test
                         (4000, [("github", 1000), ("yc", 1000), ("hn", 1000), ("rss", 334)])):
    limits.clear()
    pipeline.collect_signals(dict(settings, max_signals=wanted, use_cache=False))
    assert limits == expected, (wanted, limits)
github_service.get_trending_repositories, yc_service.get_yc_companies, hn_service.get_hn_stories, rss_service.get_rss_items = real_fetchers
page = []
github_service.requests.get = lambda url, params=None, **kw: page.append(params) or fake_get(url, params, **kw)
github_service.get_trending_repositories(limit=1000, technology_area="All", days=30)
assert page[0]["per_page"] == 100, "GitHub gives at most 100 per page"
github_service.requests.get = fake_get
print("OK nothing is capped: every technology is rated, every source fetches enough for the signals asked for")

# YC sorting by batch
yc = yc_service.get_yc_companies(limit=1, technology_area="AI / LLM")
assert "NewCo" in yc[0]["title"], f"YC should prefer newest batch, got {yc[0]['title']}"

# storage + comparison
storage.save_run(result)
second = dict(result, run_at=(datetime.now() + timedelta(seconds=5)).isoformat(timespec="seconds"))
second["technologies"] = [dict(t, ring="Hold") if t["name"] == "MCP" else t for t in result["technologies"]]
assert storage.previous_run(second)["run_at"] == result["run_at"]
st2 = storage.compare(result, second)
assert st2["MCP"] == "Moved Out" and st2["OpenTelemetry"] == "No Change", st2
st1 = storage.compare(None, result)
assert st1["OpenTelemetry"] == "New" and st1["Kubernetes"] == "New", st1

# radar + exports
svg, numbered = radar_chart.radar_html(result["technologies"], st1)
assert svg.count('class="blip"') == len(result["technologies"])
csv_text = export.byor_csv(numbered, st1)
md = export.markdown_report(result, st1, numbered)
assert csv_text.splitlines()[0] == "name,ring,quadrant,isNew,status,description"
assert "signals from GitHub, Y Combinator, Hacker News, RSS feeds · package registries checked" in md, "registries add no signals"
open(os.path.join(config.DATA_DIR, "radar_preview.html"), "w").write(svg)
print("LLM calls:", llm.calls, "| radar preview:", os.path.join(config.DATA_DIR, "radar_preview.html"))
# the single prompt: both sections are required, and a broken prompt is never saved
for broken in ("[EXTRACTION]\nfind things", "[RATING]\nrate things", "[EXTRACTION]\n\n[RATING]\nrate", "[EXTRACTION]\na\n[RATING]\nb\n[RATING]\nc"):
    try:
        ai_service.split_prompt(broken)
        raise AssertionError(broken)
    except ValueError:
        pass
assert ai_service.split_prompt("[RATING]\nrate\n[EXTRACTION]\nfind") == {"extraction": "find", "rating": "rate"}
assert "{bbv_context}" not in ai_service.classify_system() and "Items from GitHub" in ai_service.extraction_system()
print("OK one prompt for the whole app: [EXTRACTION] for every source, [RATING] for every technology")

print("ALL OFFLINE TESTS PASSED")
