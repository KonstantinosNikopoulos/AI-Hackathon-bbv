"""Offline test of the whole pipeline: no internet, no Ollama.
Run from the project folder:  python tests/test_offline.py"""
import os
import sys
import tempfile
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
from services import export, pipeline, radar_chart, rss_service, storage  # noqa: E402
from services import github_service, hn_service, yc_service  # noqa: E402
from services.radar_lanes import RISK_SCORES  # noqa: E402

pipeline.CACHE_DIR = os.path.join(config.DATA_DIR, "cache")

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


def fake_get(url, params=None, headers=None, timeout=None):
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


for module in (github_service, yc_service, hn_service, rss_service):
    module.requests.get = fake_get


class FakeLLM:
    """Pretends to be the model: finds technologies by keyword and gives rings."""
    model = "fake"

    def __init__(self):
        self.calls = 0
        self.batches = []

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
        raise AssertionError("rating no longer goes through chat_json: the agents use the chat model")


class FakeChat:
    """Stands in for ChatOllama on the rating agents: reads the technology from the prompt and answers per schema."""

    def __init__(self):
        self.asked = []   # (schema title, technology)

    def with_structured_output(self, schema, method=None):
        return FakeStructured(self, schema)


class FakeStructured:
    def __init__(self, chat, schema):
        self.chat, self.schema = chat, schema

    async def ainvoke(self, messages):
        (_, system), (_, user) = messages
        title, tech = self.schema["title"], user.splitlines()[0].replace("Technology: ", "")
        self.chat.asked.append((title, tech))
        if title.startswith("scorecard_"):
            names = [n for n in self.schema["properties"] if n not in ("summary", "confidence")]
            return {"summary": f"{title} says fine", "confidence": "medium",
                    **{n: 2 if n in RISK_SCORES else 8 if n == "maturity" else 6 for n in names}}
        if title == "risk_memo":
            return {"memo": "Risk one. Risk two. Risk three."}   # the fatal-flaw boolean is computed by code
        category = "ADOPT" if tech in ("ty", "OpenTelemetry", "MCP", "Blazor") else "HOLD" if tech == "Kubernetes" else "ASSESS"
        assert "licensing_risk is above 7" in system, "the decision matrix must be written into the judge prompt"
        return {"justification": "Because of <scores>.", "category": category, "confidence": "medium",
                "relevance": "HIGH", "business_value": "IoT & medtech"}


settings = {"area": "All", "sources": config.SOURCES, "days": 30, "max_signals": 40, "top_n": 10,
            "model": "fake", "host": "x"}
config.RSS_FEEDS[:] = ["https://feed.infoq.com/", "https://martinfowler.com/feed.atom", "https://broken.example/feed"]
pipeline.RSS_FEEDS = config.RSS_FEEDS
llm = FakeLLM()
chat = FakeChat()
msgs = []
result = pipeline.run_scan(settings, llm, lambda m, f: msgs.append((round(f, 2), m)), chat_model=chat)

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
assert techs["ty"]["ring"] == "Assess" and any("young" in n or "days old" in n for n in techs["ty"]["rule_notes"]), "young repo rule"
assert techs["ty"]["llm_ring"] == "Trial" and any("no reliable RSS data" in n for n in techs["ty"]["rule_notes"]), "matrix: Adopt needs maturity"
assert techs["OpenTelemetry"]["ring"] == "Trial" and techs["OpenTelemetry"]["llm_ring"] == "Trial", "no RSS data -> max Trial"
assert techs["MCP"]["ring"] == "Trial" and techs["MCP"]["llm_ring"] == "Trial", "a single RSS article is low confidence -> max Trial"
assert techs["Blazor"]["ring"] == "Trial" and techs["Blazor"]["llm_ring"] == "Adopt", "Adopt needs 3+ mentions"
assert any("3+ mentions" in n for n in techs["Blazor"]["rule_notes"])
assert techs["Kubernetes"]["is_seed"] and techs["Kubernetes"]["ring"] == "Adopt", "seed ring must win over the model"
assert techs["Kubernetes"]["mentions"] == 1 and any("Kept bbv" in n for n in techs["Kubernetes"]["rule_notes"])
assert techs["TypeScript"]["is_seed"] and techs["TypeScript"]["mentions"] == 0, "unmatched seeds still added"

# one prompt per source: every batch holds one source and gets that source's system prompt
print("BATCHES:", *[f"  {head} -> system starts: {sys_line[:60]}" for head, sys_line, _ in llm.batches], sep="\n")
expected = {"GitHub": "GitHub repositories", "Y Combinator": "Y Combinator", "Hacker News": "Hacker News", "RSS feeds": "engineering blogs"}
for head, sys_line, user in llm.batches:
    source = head.replace("Items from ", "").rstrip(":")
    assert expected[source] in sys_line, (source, sys_line)
gh_batch = next(u for h, _, u in llm.batches if "GitHub" in h)
assert "language: Rust" in gh_batch and "stars" in gh_batch, gh_batch
hn_batch = next(u for h, _, u in llm.batches if "Hacker News" in h)
assert "points" in hn_batch and "link: example.com" in hn_batch, hn_batch
assert set(result["settings"]["prompts"]) == {"github", "ycombinator", "hackernews", "rss", "_rules", "classify",
                                              "rate_github", "rate_ycombinator", "rate_hackernews", "rate_rss", "antihype", "judge"}

# rating agents: only sources with data run, then one anti-hype and one judge call per technology
rated = [t for t in result["technologies"] if "scorecards" in t]   # rated by the agents (a seed that matched is rated too)
assert len(rated) == 5 and all(t["scorecards"] and t["risk_memo"] for t in rated), "every rated technology carries its scorecards and memo"
assert not any("scorecards" in t for t in result["technologies"] if t["is_seed"] and t["mentions"] == 0), "unmatched seeds are not rated"
assert [c["lane"] for c in techs["ty"]["scorecards"]] == ["github"], techs["ty"]["scorecards"]
assert techs["ty"]["scorecards"][0]["scores"]["licensing_risk"] == 1, "MIT license -> computed licensing_risk 1"
assert techs["ty"]["evidence"][0]["meta"]["stars"] == 4200, "evidence keeps meta for the agents"
assert sorted(c["lane"] for c in techs["OpenTelemetry"]["scorecards"]) == ["github", "hackernews"], "its RSS copy was a duplicate"
assert sorted(c["lane"] for c in techs["MCP"]["scorecards"]) == ["hackernews", "rss"]
assert [c["lane"] for c in techs["Blazor"]["scorecards"]] == ["rss"] and techs["Blazor"]["scorecards"][0]["confidence"] == "medium"
assert {c["lane"]: c["confidence"] for c in techs["MCP"]["scorecards"]} == {"hackernews": "low", "rss": "low"}, "one story / one article"
assert len(chat.asked) == sum(len(t["scorecards"]) + 2 for t in rated), chat.asked
print("AGENT CALLS:", len(chat.asked), "| extraction calls:", llm.calls)

# source cache: a second scan with the network down reuses the fetched data
for module in (github_service, yc_service, hn_service, rss_service):
    module.requests.get = lambda *a, **k: (_ for _ in ()).throw(Exception("offline"))
again = pipeline.run_scan(settings, FakeLLM(), chat_model=FakeChat())
assert len(again["signals"]) == len(result["signals"]) and not again["errors"][1:], again["errors"]
for module in (github_service, yc_service, hn_service, rss_service):
    module.requests.get = fake_get
assert {t["quadrant"] for t in result["technologies"]} == set(config.QUADRANTS), "all quadrants filled"
assert msgs[-1][0] == 1.0

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
assert st1["OpenTelemetry"] == "New" and st1["Kubernetes"] == "", st1

# radar + exports
svg, numbered = radar_chart.radar_html(result["technologies"], st1)
assert svg.count('class="blip"') == len(result["technologies"])
csv_text = export.byor_csv(numbered, st1)
md = export.markdown_report(result, st1, numbered)
assert csv_text.splitlines()[0] == "name,ring,quadrant,isNew,status,description"
open(os.path.join(config.DATA_DIR, "radar_preview.html"), "w").write(svg)
print("LLM calls:", llm.calls, "| radar preview:", os.path.join(config.DATA_DIR, "radar_preview.html"))
print("ALL OFFLINE TESTS PASSED")
