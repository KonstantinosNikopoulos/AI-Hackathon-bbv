"""Offline test of the rating agents (services/radar_graph.py, radar_lanes.py): no internet, no Ollama.
Run from the project folder:  python tests/test_graph.py"""
import asyncio
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
from services import ai_service, radar_graph as rg, radar_record as rr  # noqa: E402
from services.radar_lanes import (LANES, LANE_SOURCE, RISK_SCORES, SCORES_BY_LANE, _count, cap_confidence,  # noqa: E402
                                  clean_scores, lane_facts, lane_schema, license_risk)

TODAY = date(2026, 10, 2)


def ev(source, i=0, **meta):
    return {"source": source, "title": f"{source}-{i}", "url": f"https://x/{source}/{i}", "text": "text", "date": "2026-09-30",
            "meta": meta}


def cand(*sources, name="Foo", own=()):
    return {"name": name, "key": name.lower(), "what": "A foo.", "quadrant": "Tools", "mentions": len(sources),
            "sources": list(dict.fromkeys(sources)), "signal": 3, "youngest_repo_days": None, "own_repos": list(own),
            "evidence": [ev(s, i) for i, s in enumerate(sources)]}


def card(lane, _confidence="high", **scores):
    names = SCORES_BY_LANE[lane] + rg.COMPUTED_SCORES.get(lane, ())
    return {"lane": lane, "status": "scored", "scores": {n: scores.get(n, 5) for n in names}, "confidence": _confidence,
            "summary": lane, "metrics": {}}


VERDICT = {"category": "TRIAL", "justification": "j", "confidence": "medium", "relevance": "HIGH", "business_value": "b"}


# ------------------------------------------------------------------ constants
assert set(rg.RING_BY_CODE.values()) == set(config.RINGS) and rg.RING_ORDER == [r.upper() for r in config.RINGS]
assert set(LANES) == set(LANE_SOURCE) == set(SCORES_BY_LANE) and all(len(v) == 2 for v in SCORES_BY_LANE.values())
assert set(LANE_SOURCE.values()) == set(config.SOURCES), "one rating agent per source in config.SOURCES, no more"
assert LANES == ("github", "ycombinator", "hackernews", "rss", "packages") and LANE_SOURCE["packages"] == config.REGISTRY_SOURCE
assert SCORES_BY_LANE["packages"] == ("production_usage_score", "integration_velocity_score"), "the names of the spec"
assert os.getenv("RADAR_LANE_CONCURRENCY") or rg.LANE_CONCURRENCY >= len(LANES), "by default all source agents of a technology run at once"
assert RISK_SCORES <= {s for lane in LANES for s in SCORES_BY_LANE[lane] + rg.COMPUTED_SCORES.get(lane, ())}
for name in [*ai_service.RATING_PROMPTS]:
    assert ai_service.load_prompt(name), name
spec = ai_service.load_prompt("rate_packages")   # the system prompt of the fifth agent is the spec's text, word for word
assert "rate_packages" in ai_service.RATING_PROMPTS and spec.startswith("You are a supply chain and dependency analyst. I will provide API")
assert ('"production_usage_score" (1-10, where 10 means massive, sustained industry-wide downloads)\n'
        '- "integration_velocity_score" (1-10, measuring the growth rate of downloads/pulls)\n'
        '- "summary" (A 2-sentence justification of the scores, highlighting specific download milestones or dependency gravity)') in spec
assert "Do not assign a Tech Radar category. Output a JSON object with:" in spec and "Official vs. unofficial Docker image pull counts." in spec
assert "{" not in rg.judge_system(), "judge prompt placeholders ({bbv_context}, {matrix}) must be filled"
assert "above 7" in rg.judge_system() and "HOLD" in rg.judge_system()
print("OK constants and prompts")

# ------------------------------------------------------------------ facts computed in code
assert [license_risk(x) for x in ("MIT", "Apache-2.0", "MPL-2.0", "GPL-3.0", "AGPL-3.0", "SSPL-1.0", "none", "NOASSERTION", "AGPL-1.0")] \
    == [1, 1, 3, 6, 9, 9, 7, 5, 9]
repo = dict(name="foo", full_name="a/foo", stars=1000, created_at="2026-09-22", pushed_at="2026-10-01", forks=50, open_issues=7,
            license="AGPL-3.0", archived=False, language="Go", description="d")
c = cand("GitHub", "GitHub", own=["https://x/GitHub/0"])
c["evidence"] = [ev("GitHub", 0, **repo), ev("GitHub", 1, **dict(repo, full_name="b/plugin", license="MIT"))]
f = lane_facts("github", c, c["evidence"], TODAY)
first = f.metrics["repos"][0]
assert first["own_repo"] and first["age_days"] == 10 and first["stars_per_day"] == 100.0 and first["days_since_push"] == 1
assert f.computed == {"licensing_risk": 9}, "only the own repo's license counts (the plugin's MIT must not hide the AGPL)"
c["evidence"][0]["meta"].pop("license")   # data cached before the license field existed: unknown, not "safe"
assert lane_facts("github", c, c["evidence"], TODAY).computed == {}
assert "own repo = yes" in f.text and "stars_per_day 100.0" in f.text
hn = lane_facts("hackernews", cand(), [ev("Hacker News", 0, points=420, comments=120, domain="a.com"),
                                         ev("Hacker News", 1, points=200, comments=30, domain="b.com")], TODAY)
assert hn.metrics["total_points"] == 620 and hn.metrics["top_points"] == 420 and hn.metrics["domains"] == ["a.com", "b.com"]
yc = lane_facts("ycombinator", cand(), [ev("Y Combinator", 0, company="A", batch="Summer 2026", tags=["AI", "Dev"], one_liner="x"),
                                          ev("Y Combinator", 1, company="B", batch="Winter 2026", tags=["AI"], one_liner="y")], TODAY)
assert yc.metrics["startups"] == 2 and yc.metrics["top_tags"][0] == "AI"
rss = lane_facts("rss", cand(), [dict(ev("RSS feeds", 0), url="https://www.infoq.com/a", date="2026-09-01"),
                                  dict(ev("RSS feeds", 1), url="https://www.cncf.io/b", date="2026-09-20")], TODAY)
assert rss.metrics["sites"] == ["cncf.io", "infoq.com"] and rss.metrics["oldest"] == "2026-09-01"
assert clean_scores({"a": 15, "b": -3, "c": "high", "d": True, "e": 6.6}, "abcde") == {"a": 10, "b": 0, "c": None, "d": None, "e": 7}
assert clean_scores({"a": 9, "b": 9}, "ab", {"a": 6}) == {"a": 6, "b": 9}, "limits cap a score the prompt promised to cap"
assert [cap_confidence(c, "medium") for c in ("high", "medium", "low")] == ["medium", "medium", "low"]

# what the data allows (code), whatever the model claims
full = dict(repo, license="MIT")
c2 = cand("GitHub", own=["https://x/GitHub/0"])
c2["evidence"] = [ev("GitHub", 0, **full)]
f2 = lane_facts("github", c2, c2["evidence"], TODAY)
assert f2.confidence_cap == "high" and f2.limits == {"project_health": 6}, "young repo: project_health can't exceed 6"
c2["evidence"][0]["meta"].pop("forks")
assert lane_facts("github", c2, c2["evidence"], TODAY).confidence_cap == "medium", "missing numbers -> medium"
assert lane_facts("github", cand("GitHub"), [ev("GitHub", 0, **full)], TODAY).confidence_cap == "low", "no own repo -> low"
old = dict(full, created_at="2025-01-01")
c2["evidence"] = [ev("GitHub", 0, **old)]
assert lane_facts("github", c2, c2["evidence"], TODAY).limits == {}, "a repository older than 180 days is not capped"
one_hn, two_hn = [ev("Hacker News", 0, points=300)], [ev("Hacker News", i, points=300) for i in range(2)]
assert lane_facts("hackernews", cand(), one_hn, TODAY).confidence_cap == "low", "a single story is thin"
assert lane_facts("hackernews", cand(), two_hn, TODAY).confidence_cap == "medium", "titles only: never high"
assert [lane_facts("ycombinator", cand(), [ev("Y Combinator", i) for i in range(n)], TODAY).confidence_cap for n in (1, 2, 3, 6)] \
    == ["low", "low", "medium", "medium"]
site = lambda i, host: dict(ev("RSS feeds", i), url=f"https://{host}/{i}")  # noqa: E731
assert lane_facts("rss", cand(), [site(0, "a.com")], TODAY).confidence_cap == "low"
assert lane_facts("rss", cand(), [site(0, "a.com"), site(1, "a.com")], TODAY).confidence_cap == "medium"
assert lane_facts("rss", cand(), [site(0, "a.com"), site(1, "b.com")], TODAY).confidence_cap == "high"
schema = lane_schema("rss")
assert list(schema["properties"])[0] == "summary" and set(schema["required"]) == {"summary", "enterprise_traction", "maturity", "confidence"}
print("OK lane facts, license risk, schemas")


# ------------------------------------------------------------------ the Package registries agent (the fifth source)
def pkg_ev(**meta):
    return ev(config.REGISTRY_SOURCE, 0, **meta)   # what services/registry_service.py produces


growing = [10_000_000, 11_000_000, 12_100_000, 13_300_000, 14_600_000, 16_100_000]
npm_meta = dict(registry="npm", package="foo", match="verified", official=False, last_30d=growing[-1], monthly=growing,
                window_days=360, window_total=sum(growing), dependent_packages=1200, dependent_repos=66000, versions=64,
                latest="1.9.1", latest_release="2026-03-25", first_release="2020-02-05", repository="a/foo")
docker_meta = dict(registry="docker", package="library/redis", match="official", official=True, lifetime=11_371_016_757,
                   stars=13620, first_release="2014-06-05", latest_release="2026-09-29")
maven_meta = dict(registry="maven", package="io.x:y", match="curated", official=False, last_30d=None, monthly=[], window_days=None,
                  window_total=None, dependent_packages=624, dependent_repos=1029, versions=104, latest="1.66.0",
                  latest_release="2026-09-12", first_release="2019-11-19")
young_npm = dict(npm_meta, monthly=[2_000_000, 6_000_000], last_30d=6_000_000, window_total=8_000_000)

f = lane_facts("packages", cand(), [pkg_ev(**npm_meta)], TODAY)
assert "npm foo | match: its repository link is the technology's GitHub repository | downloads in the last 30 days 16.1M" in f.text
assert "monthly downloads, oldest to newest: 10M, 11M, 12.1M, 13.3M, 14.6M, 16.1M" in f.text
assert "month-over-month: +10%, +10%, +10%, +10%, +10%" in f.text, f.text
assert "total over the last 360 days 77.1M" in f.text and "dependents: 1.2K packages, 66K GitHub repositories" in f.text
assert "64 versions, latest 1.9.1 released 2026-03-25" in f.text
assert f.metrics["packages"][0]["month_over_month"] == [10, 10, 10, 10, 10] and f.metrics["packages"][0]["package"] == "foo"
assert (f.confidence_cap, f.limits, f.unscored, f.computed) == ("high", {}, (), {}), "six months of downloads: a real trend"

# not enough history: a young package cannot show "sustained" use, and one month has no growth rate
y = lane_facts("packages", cand(), [pkg_ev(**young_npm)], TODAY)
assert y.limits == {"production_usage_score": 7} and y.unscored == () and y.confidence_cap == "medium" and "month-over-month: +200%" in y.text
one = lane_facts("packages", cand(), [pkg_ev(**dict(npm_meta, monthly=[6_000_000], last_30d=6_000_000, window_total=6_000_000))], TODAY)
assert one.unscored == ("integration_velocity_score",) and one.limits == {"production_usage_score": 7} and "month-over-month" not in one.text

# Docker Hub: lifetime pulls, official vs unofficial, no trend
d = lane_facts("packages", cand(), [pkg_ev(**docker_meta)], TODAY)
assert "Docker Hub library/redis (OFFICIAL image) | match: Docker official image | lifetime pulls 11.4B | stars 13620" in d.text
assert d.limits == {} and d.unscored == ("integration_velocity_score",) and d.confidence_cap == "medium", "a 12 year old image, but no trend"
assert "(unofficial image)" in lane_facts("packages", cand(), [pkg_ev(**dict(docker_meta, official=False, match="owner"))], TODAY).text
# an old Docker image shows sustained use even when the npm package is new; one trend is enough to score growth
mixed = lane_facts("packages", cand(), [pkg_ev(**young_npm), pkg_ev(**docker_meta)], TODAY)
assert mixed.limits == {} and mixed.unscored == () and mixed.text.count("\n- ") == 2 and mixed.confidence_cap == "medium"

# Maven Central: no downloads, only dependents and releases
mv = lane_facts("packages", cand(), [pkg_ev(**maven_meta)], TODAY)
assert "no download counts are published" in mv.text and "dependents: 624 packages, 1K GitHub repositories" in mv.text
assert (mv.confidence_cap, mv.limits, mv.unscored) == ("medium", {}, ("integration_velocity_score",))
nothing = lane_facts("packages", cand(), [pkg_ev(**dict(maven_meta, dependent_packages=None, dependent_repos=None))], TODAY)
assert nothing.confidence_cap == "low" and "dependents unknown" in nothing.text

# the answer has exactly the keys of the spec, scores run from 1 to 10, the other agents still from 0
schema = lane_schema("packages")
assert list(schema["properties"]) == ["summary", "production_usage_score", "integration_velocity_score"], "no confidence field"
assert schema["required"] == list(schema["properties"]) and schema["title"] == "scorecard_packages"
assert all(schema["properties"][n]["minimum"] == 1 and schema["properties"][n]["maximum"] == 10 for n in SCORES_BY_LANE["packages"])
assert lane_schema("rss")["properties"]["maturity"]["minimum"] == 0
assert clean_scores({"a": 0, "b": 15, "c": 3}, "abc", floor=1) == {"a": 1, "b": 10, "c": 3}
assert [_count(n) for n in (None, 0, 999, 1000, 1234, 999_499, 999_999, 12_100_000, 999_999_999, 11_371_016_757)] \
    == ["unknown", "0", "999", "1K", "1.2K", "999.5K", "1M", "12.1M", "1B", "11.4B"], "sizes, never '1000K'"
print("OK package registry facts: trend, caps, unknowns, confidence from the data, the spec's schema")

# ------------------------------------------------------------------ the graph hands the judge FACTS; the guards live in radar_record
for gone in ("enforce_matrix", "find_fatal_flaws", "flat_scores", "ADOPT_MIN_MATURITY", "ASSESS_CAP_IF_ANY_RISK_AT_LEAST",
             "FATAL_IF_HEALTH_OR_MATURITY_AT_MOST", "HOLD_IF_LICENSING_RISK_ABOVE"):
    assert not hasattr(rg, gone), f"{gone}: a veto from the scores of a model (friction, hype, maturity) is gone, see tests/test_record.py"
assert rg.matrix_rules is rr.matrix_rules and "above 7" in rg.judge_system() and "archived" in rg.judge_system()

OLD_REPO = dict(name="foo", full_name="a/foo", stars=50_000, created_at="2012-01-01", pushed_at="2026-10-01", forks=9, open_issues=3,
                license="MIT", archived=False, language="C", description="d")


def established(*sources, name="Foo", **repo_meta):
    """A candidate with a big old own repository (standing: widespread) plus one evidence item per source."""
    c = cand(*sources, name=name)
    repo_item = dict(ev("GitHub", 90, **dict(OLD_REPO, **repo_meta)), url="https://github.com/a/foo", date="2012-01-01")
    c["evidence"] = [repo_item] + c["evidence"]
    c["own_repos"] = [repo_item["url"]]
    return c


weak = lambda lane, **s: card(lane, "low", **s)  # noqa: E731
failed = {"lane": "rss", "status": "error", "scores": {"enterprise_traction": None, "maturity": None}, "confidence": "low",
          "summary": "", "metrics": {}}

# what the judge reads: the facts, ALL the evidence (the same lines as the single prompt), the scorecards, and a memo only when there is one
young = cand("RSS feeds", "RSS feeds")
memo = {"fatal_flaws_found": False, "memo": "Memo one. Two. Three."}
text = rg.judge_input(young, [card("rss")], memo)
assert text.startswith("Technology: Foo\nWhat it is: A foo.\nSuggested quadrant: Tools\n")
assert "Facts measured by code (true, do not dispute them):\n- Standing: unknown (nothing shows how old it is)." in text
assert text.index("Facts measured by code") < text.index("Evidence (every item") < text.index("Scorecards from the source analysts") < text.index("Skeptic's")
assert all(line in text for line in ai_service.evidence_lines(young)) and ai_service.evidence_lines(young)[0] == "- 2026-09-30 RSS feeds: RSS feeds-0 - text"
assert "- RSS feeds agent [high confidence]: " in text and "GitHub agent: NO USABLE DATA" in text
assert "Skeptic's risk memo (fatal flaws found by code: no):\nMemo one. Two. Three." in text
assert "fatal flaws found by code: YES" in rg.judge_input(young, [card("rss")], dict(memo, fatal_flaws_found=True))
unreviewed = rg.judge_input(young, [card("rss")], {"fatal_flaws_found": False, "memo": ""})
assert "Skeptic's risk memo: it did not run, so the risks are unchecked." in unreviewed, "a technology that needs a skeptic and has none: say so"
big = established("Hacker News")
single = ai_service.classify_message(big) + "\n\nFacts measured by code (true, do not dispute them):\n" + rr.format_record(rr.build_record(big))
assert "Its own GitHub repository a/foo: 50k stars, created 2012-01-01" in single and "- 2012-01-01 GitHub: GitHub-90 - text" in single
assert "Standing: widespread (years old and used at scale)" in single and "Scorecards" not in single, "an established technology: facts + evidence only"
print("OK the judge reads facts, all the evidence, scorecards and the memo; the single prompt reads facts and evidence")


# ------------------------------------------------------------------ graph flow with fake agents
log = {"eval": [], "memo": 0, "decide": [], "direct": [], "active": 0, "max_active": 0}


def reset():
    log.update(eval=[], memo=0, decide=[], direct=[], active=0, max_active=0)


async def fake_eval(llm, payload):
    log["eval"].append(payload["lane"])
    log["active"] += 1
    log["max_active"] = max(log["max_active"], log["active"])
    await asyncio.sleep(0.05)
    log["active"] -= 1
    return card(payload["lane"])


async def fake_memo(llm, candidate, scorecards):
    log["memo"] += 1
    return {"fatal_flaws_found": True, "memo": "Risk one. Risk two. Risk three."}


async def fake_decide(llm, candidate, scorecards, risk):
    log["decide"].append((sorted(c["lane"] for c in scorecards), risk))
    return dict(VERDICT)


async def fake_direct(llm, candidate):
    log["direct"].append(candidate["name"])
    return dict(VERDICT)


def all_lanes(candidate):
    return rg.initial_state(dict(candidate, evidence=[ev(s, i) for i, s in enumerate(LANE_SOURCE.values())]))


async def flow():
    graph = rg.build_graph(llm=object())
    mermaid = graph.get_graph().draw_mermaid()
    assert all(f"{lane} --> anti_hype" in mermaid for lane in LANES) and "anti_hype --> judge" in mermaid
    cfg = {"max_concurrency": rg.LANE_CONCURRENCY}
    rg.evaluate_lane, rg.write_risk_memo, rg.decide, rg.decide_direct = fake_eval, fake_memo, fake_decide, fake_direct

    # five agents really run at the same time (a 5-party barrier would deadlock if they ran one after another)
    gate = asyncio.Barrier(len(LANES))

    async def barrier_eval(llm, payload):
        await asyncio.wait_for(gate.wait(), 5)
        return card(payload["lane"])

    rg.evaluate_lane = barrier_eval
    out = await graph.ainvoke(all_lanes(cand()), config={"max_concurrency": len(LANES)})
    assert sorted(c["lane"] for c in out["scorecards"]) == sorted(LANES) and out["errors"] == []
    assert out["fatal_flaws_found"] is True and out["risk_memo"].startswith("Risk one")
    assert out["verdict"]["category"] == "TRIAL" and out["rule_notes"] == [], "the judge's category is returned as the model gave it"
    print("OK 5 source agents in parallel, scorecards merged, anti-hype + judge; the guards run later, in pipeline.apply_rules")

    # only sources with data run; the skeptic and the judge run once
    reset(); rg.evaluate_lane = fake_eval
    out = await graph.ainvoke(rg.initial_state(cand("GitHub", "RSS feeds", "RSS feeds")), config=cfg)
    assert sorted(log["eval"]) == ["github", "rss"] and log["memo"] == 1 and len(log["decide"]) == 1
    assert log["decide"][0][0] == ["github", "rss"]
    print("OK partial dispatch: 2 sources -> 2 agent calls, skeptic once, judge once")

    # an established technology skips the map phase and the skeptic: one judge call, no scorecards (the model knows it, and the digests of
    # agents that see one source each cost 21 points on the gold set); code still sees an archived repository
    reset()
    state = rg.initial_state(established("Hacker News"))
    assert rg.route_to_evaluators(state) == ["judge"]
    out = await graph.ainvoke(state, config=cfg)
    assert log["eval"] == [] and log["memo"] == 0 and out["scorecards"] == [] and out["risk_memo"] == "" and out["errors"] == []
    assert log["decide"] == [] and log["direct"] == ["Foo"] and out["verdict"]["category"] == "TRIAL", "the single prompt, not the judge"
    # old but nobody uses it: not established, so the full pipeline runs, skeptic included
    reset()
    state = rg.initial_state(established("Hacker News", stars=300))
    assert sorted(s_.node for s_ in rg.route_to_evaluators(state)) == ["github", "hackernews"]
    await graph.ainvoke(state, config=cfg)
    assert sorted(log["eval"]) == ["github", "hackernews"] and log["memo"] == 1 and log["decide"][0][0] == ["github", "hackernews"] and log["direct"] == []
    print("OK routing: an established technology goes straight to the judge, the others run the map phase and the skeptic")

    # the fifth lane is routed like the others: registry evidence alone starts it, and without evidence it never runs
    reset()
    state = rg.initial_state(cand("GitHub", "Package registries"))
    assert sorted(state["raw"]) == ["github", "packages"] and state["raw"]["packages"]["evidence"][0]["source"] == config.REGISTRY_SOURCE
    assert [s.node for s in rg.route_to_evaluators(state)] == ["github", "packages"]
    out = await graph.ainvoke(state, config=cfg)
    assert sorted(log["eval"]) == ["github", "packages"] and log["decide"][0][0] == ["github", "packages"]
    reset()
    await graph.ainvoke(rg.initial_state(cand("Package registries")), config=cfg)
    assert log["eval"] == ["packages"] and log["memo"] == 1 and len(log["decide"]) == 1, "registry data alone is enough to rate"
    reset()
    await graph.ainvoke(rg.initial_state(cand("GitHub", "Hacker News", "RSS feeds", "Y Combinator")), config=cfg)
    assert "packages" not in log["eval"] and len(log["eval"]) == 4, "no registry evidence, no fifth agent call"
    print("OK packages lane: dispatched by registry evidence only, alone or with the others")

    # nothing to rate -> straight to the judge fallback, zero LLM calls
    reset()
    out = await graph.ainvoke(rg.initial_state(cand()), config=cfg)
    assert log["eval"] == [] and log["memo"] == 0 and log["decide"] == []
    assert out["verdict"]["category"] == "ASSESS" and out["verdict"]["confidence"] == "low"
    print("OK no data -> ASSESS/low, zero LLM calls")

    # one agent raising is isolated and named in errors; the judge never sees its card
    reset()

    async def flaky(llm, payload):
        if payload["lane"] == "github":
            raise RuntimeError("ollama timeout")
        return await fake_eval(llm, payload)

    rg.evaluate_lane = flaky
    out = await graph.ainvoke(rg.initial_state(cand("GitHub", "Hacker News", "RSS feeds")), config=cfg)
    by = {c["lane"]: c for c in out["scorecards"]}
    assert by["github"]["status"] == "error" and by["github"]["scores"] == {"developer_velocity": None, "project_health": None,
                                                                          "licensing_risk": None}
    assert out["errors"] == ["Foo / github: ollama timeout"] and log["decide"][0][0] == ["hackernews", "rss"]
    print("OK failed agent isolated, judge sees only scored cards")

    # every agent fails -> fallback without the skeptic or the judge
    reset()

    async def down(llm, payload):
        raise RuntimeError("down")

    rg.evaluate_lane = down
    out = await graph.ainvoke(rg.initial_state(cand("GitHub", "RSS feeds")), config=cfg)
    assert out["verdict"]["category"] == "ASSESS" and log["memo"] == 0 and log["decide"] == [] and len(out["errors"]) == 2
    print("OK all agents failed -> ASSESS/low")

    # the skeptic failing is reported, and an empty memo tells the judge "not reviewed"
    reset(); rg.evaluate_lane = fake_eval

    async def bad_memo(llm, candidate, scorecards):
        raise RuntimeError("memo broke")

    rg.write_risk_memo = bad_memo
    out = await graph.ainvoke(rg.initial_state(cand("GitHub")), config=cfg)
    assert out["risk_memo"] == "" and out["fatal_flaws_found"] is False and out["errors"] == ["Foo / anti_hype: memo broke"]
    assert log["decide"][0][1] == {"fatal_flaws_found": False, "memo": ""}
    rg.write_risk_memo = fake_memo
    print("OK anti-hype failure recorded, judge still decides")

    # the parallelism cap really limits requests in flight
    reset()
    await graph.ainvoke(all_lanes(cand()), config={"max_concurrency": 2})
    assert log["max_active"] == 2, log["max_active"]
    print("OK max_concurrency=2 -> at most 2 agents in flight")

    # adapter: exactly the old classify keys, ring in config.RINGS case
    answer = rg.to_classification(cand("GitHub"), VERDICT)
    assert set(answer) == set(ai_service.CLASSIFY_SCHEMA["required"]) and answer["ring"] == "Trial"
    print("OK to_classification matches the old classify keys")

    # rate_all: technologies in flight are capped, each reported, one failure doesn't lose the others
    reset()
    inflight = {"now": 0, "max": 0}
    done = []

    async def slow_decide(llm, candidate, scorecards, risk):
        inflight["now"] += 1
        inflight["max"] = max(inflight["max"], inflight["now"])
        await asyncio.sleep(0.05)
        inflight["now"] -= 1
        if candidate["name"] == "Bad":
            raise RuntimeError("judge failed")
        return dict(VERDICT)

    rg.decide = slow_decide
    cands = [cand("GitHub", name=n) for n in ("A", "Bad", "C")]
    results = await rg.rate_all(graph, cands, on_done=lambda i, c: done.append(c["name"]))
    assert inflight["max"] == 1 and sorted(done) == ["A", "Bad", "C"], (inflight, done)
    assert results[0]["answer"]["ring"] and isinstance(results[1], RuntimeError) and results[2]["answer"]
    rg.TECH_CONCURRENCY = 3
    inflight.update(now=0, max=0)
    await rg.rate_all(graph, [cand("GitHub", name=n) for n in "XYZ"])
    assert inflight["max"] == 3, inflight
    rg.TECH_CONCURRENCY = config.GRAPH_TECH_CONCURRENCY
    assert await rg.rate_all(graph, []) == []
    print("OK rate_all: concurrency cap, progress callback, failure returned in place, empty list")


REAL = (rg.evaluate_lane, rg.write_risk_memo, rg.decide, rg.decide_direct)
asyncio.run(flow())
rg.evaluate_lane, rg.write_risk_memo, rg.decide, rg.decide_direct = REAL   # flow() swapped in fakes


# ------------------------------------------------------------------ the real LLM functions against hostile model output
class FakeChat:
    def __init__(self, replies):
        self.replies, self.seen = replies, []

    def with_structured_output(self, schema, method=None):
        title = schema.get("title", "classify")   # the single prompt's schema (ai_service.CLASSIFY_SCHEMA) has no title
        assert method == "json_schema" and title in self.replies
        chat = self

        class Runnable:
            async def ainvoke(self, messages):
                chat.seen.append((title, messages))
                reply = chat.replies[title]
                if isinstance(reply, Exception):
                    raise reply
                return reply

        return Runnable()


async def hostile():
    payload = {"lane": "rss", "candidate": cand("RSS feeds"), "lane_raw": {"evidence": [ev("RSS feeds")]}}
    # out-of-range, wrong types and an unknown confidence are cleaned, not crashed on
    chat = FakeChat({"scorecard_rss": {"summary": " s ", "enterprise_traction": 15, "maturity": "high", "confidence": "certain"}})
    card_ = await rg.evaluate_lane(chat, payload)
    assert card_["scores"] == {"enterprise_traction": 10, "maturity": None} and card_["confidence"] == "low" and card_["summary"] == "s"
    # the model cannot claim more confidence than the data allows (one article -> low), nor break the young-repo cap
    claims_high = {"summary": "s", "enterprise_traction": 9, "maturity": 9, "confidence": "high"}
    assert (await rg.evaluate_lane(FakeChat({"scorecard_rss": claims_high}), payload))["confidence"] == "low"
    two_sites = dict(payload, lane_raw={"evidence": [dict(ev("RSS feeds", 0), url="https://a.com/1"), dict(ev("RSS feeds", 1), url="https://b.com/2")]})
    assert (await rg.evaluate_lane(FakeChat({"scorecard_rss": claims_high}), two_sites))["confidence"] == "high"
    own = dict(full, created_at="2026-09-22")
    gh_payload = {"lane": "github", "candidate": cand("GitHub", own=["https://x/GitHub/0"]), "lane_raw": {"evidence": [ev("GitHub", 0, **own)]}}
    gh_card = await rg.evaluate_lane(FakeChat({"scorecard_github": {"summary": "s", "developer_velocity": 9, "project_health": 9,
                                                                   "confidence": "high"}}), gh_payload)
    assert gh_card["scores"] == {"developer_velocity": 9, "project_health": 6, "licensing_risk": 1}, gh_card["scores"]
    system, user = chat.seen[0][1]
    assert system[1] == ai_service.load_prompt("rate_rss") and "Technology: Foo" in user[1] and "Articles about this technology" in user[1]
    # no usable score at all is an error (the node then records an error scorecard), not a made-up scorecard
    try:
        await rg.evaluate_lane(FakeChat({"scorecard_rss": {"summary": "s", "enterprise_traction": "?", "maturity": None}}), payload)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    # a model that does not return an object
    try:
        await rg.evaluate_lane(FakeChat({"scorecard_rss": ["not", "an", "object"]}), payload)
        raise AssertionError("should have raised")
    except TypeError:
        pass
    # the judge: unknown category -> ASSESS, and it is shown the risk memo, the polarity and the unknown lanes
    chat = FakeChat({"verdict": {"justification": "j", "category": "MAYBE", "confidence": "?", "relevance": "?", "business_value": "b"}})
    v = await rg.decide(chat, cand("RSS feeds"), [card("rss")], {"fatal_flaws_found": True, "memo": "Bad. Worse. Worst."})
    assert v["category"] == "ASSESS" and v["confidence"] == "low" and v["relevance"] == "LOW"
    judge_user = chat.seen[0][1][1][1]
    assert "fatal flaws found by code: YES" in judge_user and "Bad. Worse. Worst." in judge_user
    assert "Facts measured by code" in judge_user and "Evidence (every item" in judge_user
    assert "GitHub agent: NO USABLE DATA" in judge_user and "unknown, not good news" in judge_user
    assert "Package registries agent: NO USABLE DATA" in judge_user, "the fifth source is named too when it has nothing"
    # an established technology is rated by the single prompt with the facts added; an unknown ring is ASSESS
    chat = FakeChat({"classify": {"quadrant": "Tools", "ring": "Adopt", "summary": "s", "reason": "Mature.", "business_value": "Observability.", "relevance": "HIGH", "confidence": "high"}})
    v = await rg.decide_direct(chat, established("GitHub"))
    assert v["category"] == "ADOPT" and v["justification"] == "Mature." and v["confidence"] == "high" and v["relevance"] == "HIGH"
    system, user = chat.seen[0][1]
    assert system[1] == ai_service.classify_system() and user[1].startswith(ai_service.classify_message(established("GitHub")))
    assert "Facts measured by code (true, do not dispute them):" in user[1] and "Standing: widespread" in user[1]
    odd = FakeChat({"classify": dict({"quadrant": "Tools", "ring": "Adopt", "summary": "s", "reason": "Mature.", "business_value": "Observability.", "relevance": "HIGH", "confidence": "high"}, ring="Maybe", relevance="?", confidence="?")})
    v = await rg.decide_direct(odd, established("GitHub"))
    assert (v["category"], v["relevance"], v["confidence"]) == ("ASSESS", "LOW", "low")
    # the skeptic: the model fills three fields that CODE joins; CODE decides fatal_flaws_found (a model once invented a "friction 9")
    three = lambda a="r1", b="r2", c="r3", **extra: {"biggest_risk": a, "second_risk_or_unknown": b, "what_must_be_true": c, **extra}  # noqa: E731
    chat = FakeChat({"risk_memo": three(" Big risk ", "Unknown.", "Proof!", fatal_flaws_found=True, memo="ignored")})   # a claimed flaw is ignored
    risk = await rg.write_risk_memo(chat, cand("RSS feeds"), [card("rss")])
    assert risk == {"fatal_flaws_found": False, "memo": "Big risk. Unknown. Proof!"}, risk   # trimmed, full stop added when missing
    assert "Fatal flaws found by code: none." in chat.seen[0][1][1][1]
    chat = FakeChat({"risk_memo": three("Licence.", "Unknowns.", "Clarify.")})   # a model that says nothing about it is overruled
    risk = await rg.write_risk_memo(chat, established("GitHub", license="AGPL-3.0"), [card("github", licensing_risk=9), card("rss")])
    assert risk["fatal_flaws_found"] is True and risk["memo"] == "Licence. Unknowns. Clarify."
    memo_user = chat.seen[0][1][1][1]
    assert "licensing_risk is 9 (license AGPL-3.0)" in memo_user and "put them first" in memo_user
    assert "Facts measured by code:" in memo_user and "Standing: widespread" in memo_user, "the skeptic is told the facts too"
    # a real scan once had qwen3:4b copy the prompt's instructions as the "memo" for EVERY technology: that is an error now
    prompt = ai_service.load_prompt("antihype")
    copied = three("The strongest reason not to use it, naming the score or the fact it comes from.", "Fine.", "Fine.")
    for reply, why in ((copied, "copied its instructions"), (three("A.", "", "C."), "left a part")):
        try:
            await rg.write_risk_memo(FakeChat({"risk_memo": reply}), cand("RSS feeds"), [card("rss")])
            raise AssertionError("should have raised")
        except ValueError as error:
            assert why in str(error), error
    ok_quote = three("The GitHub agent has NO USABLE DATA, which is an unknown.", "Maturity is unknown.", "Production use must be shown.")
    assert (await rg.write_risk_memo(FakeChat({"risk_memo": ok_quote}), cand("RSS feeds"), [card("rss")]))["memo"].startswith("The GitHub agent")
    # ... and so is a memo that states a score nobody gave (qwen3:4b wrote "developer_friction: 9" for a technology with no HN card)
    gh_card = card("github", developer_velocity=9, project_health=6, licensing_risk=1)
    for claim in ("developer_friction: 9 is high.", "The developer_velocity of 4 is low.", "licensing_risk (7) matters."):
        assert rg.invented_scores(claim, [gh_card]), claim
    for fine in ("developer_velocity 9 is high.", "Hacker News has NO USABLE DATA so developer_friction is unknown.", "project_health of 6, licensing_risk is 1."):
        assert rg.invented_scores(fine, [gh_card]) == [], fine
    try:
        await rg.write_risk_memo(FakeChat({"risk_memo": three("developer_friction: 9 is the biggest risk.", "Unknown.", "Proof.")}), cand("GitHub"), [gh_card])
        raise AssertionError("should have raised")
    except ValueError as error:
        assert "developer_friction 9" in str(error), error
    assert rg.copies_prompt("a b c d e f g h i j k", "x a b c d e f g h i j y") and not rg.copies_prompt("a b c d e f g h i", "a b c d e f g h i")
    assert not rg.copies_prompt("a short memo", prompt) and not rg.copies_prompt("", prompt), "short quotes and empty text are not copies"
    # and the node turns the error into a recorded problem, with an empty memo so the judge is told "not reviewed"
    state = rg.initial_state(cand("RSS feeds"))
    state["scorecards"] = [card("rss")]
    node = await rg.anti_hype_node(state, llm=FakeChat({"risk_memo": copied}))
    assert node["risk_memo"] == "" and node["fatal_flaws_found"] is False and "copied its instructions" in node["errors"][0], node
    print("OK real LLM functions: cleaning, errors, polarity/unknown lanes in the prompts, memo fields and the copied-prompt guard")

    # the Package registries agent: the spec's three keys, scores 1-10, confidence decided by the data, caps and unknowns in code
    def reg_payload(*metas):
        return {"lane": "packages", "candidate": cand(), "lane_raw": {"evidence": [pkg_ev(**m) for m in metas]}}

    said = {"summary": " Downloads grew every month. ", "production_usage_score": 9, "integration_velocity_score": 0, "confidence": "low"}
    chat = FakeChat({"scorecard_packages": said})
    card_ = await rg.evaluate_lane(chat, reg_payload(npm_meta))
    assert card_["scores"] == {"production_usage_score": 9, "integration_velocity_score": 1}, "0 is below the 1-10 scale"
    assert card_["confidence"] == "high", "the model's own 'low' is not used: six months of downloads decide"
    assert card_["summary"] == "Downloads grew every month." and card_["metrics"]["packages"][0]["month_over_month"] == [10] * 5
    system, user = chat.seen[0][1]
    assert system[1] == ai_service.load_prompt("rate_packages") and "Technology: Foo" in user[1] and "Package registry data" in user[1]
    young_card = await rg.evaluate_lane(FakeChat({"scorecard_packages": dict(said, production_usage_score=10)}), reg_payload(young_npm))
    assert young_card["scores"]["production_usage_score"] == 7 and young_card["confidence"] == "medium", "two months: not 'sustained'"
    pulls = await rg.evaluate_lane(FakeChat({"scorecard_packages": dict(said, integration_velocity_score=8)}), reg_payload(docker_meta))
    assert pulls["scores"] == {"production_usage_score": 9, "integration_velocity_score": None}, "no trend: unknown, not the model's guess"
    assert pulls["confidence"] == "medium"
    try:
        await rg.evaluate_lane(FakeChat({"scorecard_packages": {"summary": "s", "production_usage_score": "?",
                                                                "integration_velocity_score": 8}}), reg_payload(docker_meta))
        raise AssertionError("should have raised")   # the only score the data supports is unusable, the other is unknown
    except ValueError:
        pass
    print("OK package registries agent: 1-10 scores, confidence and caps from the data, unknown growth without a trend")

    # the whole graph with the real nodes and a scripted model: happy path, then a model that times out
    replies = {"classify": {"quadrant": "Tools", "ring": "Adopt", "summary": "s", "reason": "Mature.", "business_value": "Observability.", "relevance": "HIGH", "confidence": "high"},
               "scorecard_rss": {"summary": "InfoQ and CNCF report production use.", "enterprise_traction": 8, "maturity": 9,
                                 "confidence": "high"},
               "risk_memo": three("None.", "None.", "None."),
               "verdict": {"justification": "Mature.", "category": "ADOPT", "confidence": "high", "relevance": "HIGH",
                           "business_value": "Observability for customers."}}
    graph = rg.build_graph(FakeChat(replies))
    res = await rg.rate_candidate(graph, cand("RSS feeds", "RSS feeds"))
    assert res["answer"]["ring"] == "Adopt" and res["rule_notes"] == [] and res["errors"] == []
    assert (res["route"], res["standing"]) == ("agents", "unknown"), "nothing shows its age: the map phase and the skeptic run"
    direct = await rg.rate_candidate(graph, established("Hacker News"))
    assert (direct["route"], direct["standing"], direct["scorecards"]) == ("direct", "widespread", []) and direct["answer"]["ring"] == "Adopt"
    assert direct["fatal_flaws_found"] is False and direct["risk_memo"] == "" and direct["errors"] == []
    archived_direct = await rg.rate_candidate(graph, established("Hacker News", archived=True))
    assert archived_direct["fatal_flaws_found"] is True, "an archived repository is reported even when no skeptic ran"
    assert res["scorecards"][0]["scores"] == {"enterprise_traction": 8, "maturity": 9}
    res = await rg.rate_candidate(rg.build_graph(FakeChat(dict(replies, scorecard_rss=TimeoutError("ollama timed out")))),
                                  cand("RSS feeds"))
    assert res["answer"]["ring"] == "Assess" and res["errors"] == ["Foo / rss: ollama timed out"], res
    print("OK whole graph with the real nodes: happy path, and a model timeout degrades to Assess")

    # five lanes through the real nodes: an RSS article pair and a registry lookup are scored side by side
    new_package = dict(npm_meta, first_release="2026-08-01")   # a package that old would make the technology established: no map phase
    both = dict(cand("RSS feeds", "RSS feeds"), evidence=[ev("RSS feeds", 0), ev("RSS feeds", 1), pkg_ev(**new_package)])
    packages_reply = {"summary": "16.1M downloads a month, up 10% a month.", "production_usage_score": 8, "integration_velocity_score": 7}
    res = await rg.rate_candidate(rg.build_graph(FakeChat(dict(replies, scorecard_packages=packages_reply))), both)
    assert sorted(c["lane"] for c in res["scorecards"]) == ["packages", "rss"] and res["errors"] == [], res["errors"]
    package_card = next(c for c in res["scorecards"] if c["lane"] == "packages")
    assert package_card["scores"] == {"production_usage_score": 8, "integration_velocity_score": 7} and package_card["confidence"] == "high"
    # a registry agent that fails costs only its own card
    res = await rg.rate_candidate(rg.build_graph(FakeChat(dict(replies, scorecard_packages=TimeoutError("ollama timed out")))), both)
    failed_card = next(c for c in res["scorecards"] if c["lane"] == "packages")
    assert failed_card["status"] == "error" and failed_card["scores"] == {"production_usage_score": None, "integration_velocity_score": None}
    assert res["errors"] == ["Foo / packages: ollama timed out"] and res["answer"]["ring"] == "Adopt", "the others still decide"
    print("OK five lanes through the real nodes: registry numbers scored next to the press, a failure stays local")


asyncio.run(hostile())
print("ALL GRAPH TESTS PASSED")
