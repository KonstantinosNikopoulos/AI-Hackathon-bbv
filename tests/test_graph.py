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
from services import ai_service, radar_graph as rg  # noqa: E402
from services.radar_lanes import (LANES, LANE_SOURCE, RISK_SCORES, SCORES_BY_LANE, cap_confidence, clean_scores,  # noqa: E402
                                  lane_facts, lane_schema, license_risk)

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
assert set(LANE_SOURCE.values()) == set(config.SOURCES), "one rating agent per collected source, no more"
assert RISK_SCORES <= {s for lane in LANES for s in SCORES_BY_LANE[lane] + rg.COMPUTED_SCORES.get(lane, ())}
for name in [*ai_service.RATING_PROMPTS]:
    assert ai_service.load_prompt(name), name
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

# ------------------------------------------------------------------ decision matrix (code, not prompt)
def matrix(category, cards, fatal=False):
    verdict, notes = rg.enforce_matrix(dict(VERDICT, category=category), cards, fatal)
    return verdict["category"], notes


gh = lambda **s: card("github", **s)  # noqa: E731
rss_card = lambda **s: card("rss", **s)  # noqa: E731
assert matrix("ADOPT", [gh(licensing_risk=9), rss_card(maturity=9)])[0] == "HOLD", "licensing_risk > 7 forces HOLD"
assert matrix("ADOPT", [gh(licensing_risk=8), rss_card(maturity=9)])[0] == "HOLD"
assert matrix("ADOPT", [gh(licensing_risk=7), rss_card(maturity=9)])[0] == "ASSESS", "7 is not above 7, but a risk of 7+ caps at ASSESS"
assert matrix("TRIAL", [gh(licensing_risk=6), rss_card(maturity=9)])[0] == "TRIAL", "6 is fine"
assert matrix("ADOPT", [rss_card(maturity=9)], fatal=True)[0] == "ASSESS", "fatal flaws cap at ASSESS"
assert matrix("ADOPT", [card("hackernews", developer_friction=8), rss_card(maturity=9)])[0] == "ASSESS"
assert matrix("ADOPT", [card("ycombinator", hype_risk=7), rss_card(maturity=9)])[0] == "ASSESS"
assert matrix("ADOPT", [rss_card(maturity=8)]) == ("ADOPT", []), "enough maturity, no risk: untouched"
assert matrix("ADOPT", [rss_card(maturity=6)])[0] == "TRIAL" and matrix("ADOPT", [gh()])[0] == "TRIAL", "Adopt needs maturity >= 7"
assert matrix("HOLD", [rss_card(maturity=9)])[0] == "HOLD" and matrix("ASSESS", [gh()])[0] == "ASSESS", "never moves a ring UP"
code, notes = matrix("ADOPT", [gh(licensing_risk=9)])
assert code == "HOLD" and notes == ["Adopt → Hold: licensing_risk is 9 (above 7)"], notes
failed = {"lane": "rss", "status": "error", "scores": {"enterprise_traction": None, "maturity": None}, "confidence": "low",
          "summary": "", "metrics": {}}
assert matrix("ADOPT", [failed])[0] == "TRIAL", "a failed agent counts as no maturity score"

# confidence gating: weak model judgements must not decide the ring, computed scores always count
weak = lambda lane, **s: card(lane, "low", **s)  # noqa: E731
assert matrix("ADOPT", [weak("ycombinator", hype_risk=9), rss_card(maturity=9)]) == ("ADOPT", []), "low-confidence risk is ignored"
assert matrix("ADOPT", [weak("hackernews", developer_friction=9), rss_card(maturity=9)])[0] == "ADOPT"
assert matrix("ADOPT", [card("ycombinator", "medium", hype_risk=9), rss_card(maturity=9)])[0] == "ASSESS", "medium confidence counts"
code, notes = matrix("ADOPT", [weak("rss", maturity=9)])
assert code == "TRIAL" and "no reliable RSS data" in notes[0], "a low-confidence maturity cannot justify ADOPT"
assert matrix("ADOPT", [weak("github", licensing_risk=9), rss_card(maturity=9)])[0] == "HOLD", "computed licensing_risk always counts"
assert matrix("ADOPT", [weak("github", licensing_risk=7), rss_card(maturity=9)])[0] == "ASSESS"
assert "low confidence" in " ".join(rg.matrix_rules()) and "not low confidence" in " ".join(rg.matrix_rules())
print("OK decision matrix")

# ------------------------------------------------------------------ fatal flaws are found by code
flaws = rg.find_fatal_flaws
assert flaws([gh(), rss_card(maturity=8)]) == [], "healthy: no flaws"
assert flaws([gh(licensing_risk=9)]) == ["licensing_risk is 9 (higher is worse)"]
assert flaws([card("ycombinator", hype_risk=7)]) == ["hype_risk is 7 (higher is worse)"]
assert flaws([card("ycombinator", hype_risk=6), card("hackernews", developer_friction=6)]) == [], "6 is below the line"
assert flaws([gh(project_health=2)]) == ["project_health is only 2"] and flaws([rss_card(maturity=1)]) == ["maturity is only 1"]
assert flaws([gh(project_health=3), rss_card(maturity=3)]) == [], "3 is poor but not fatal"
assert flaws([weak("ycombinator", hype_risk=9), weak("rss", maturity=0)]) == [], "a low-confidence model score is not a fatal flaw"
assert flaws([weak("github", licensing_risk=9)]) == ["licensing_risk is 9 (higher is worse)"], "computed scores always count"
archived = dict(gh(), metrics={"repos": [{"repo": "a/foo", "own_repo": True, "archived": True},
                                           {"repo": "b/plugin", "own_repo": False, "archived": True}]})
assert flaws([archived]) == ["the repository a/foo is archived"], "only an own repository being archived counts"
assert flaws([failed]) == []
print("OK fatal flaws found by code")


# ------------------------------------------------------------------ graph flow with fake agents
log = {"eval": [], "memo": 0, "decide": [], "active": 0, "max_active": 0}


def reset():
    log.update(eval=[], memo=0, decide=[], active=0, max_active=0)


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


def all_lanes(candidate):
    return rg.initial_state(dict(candidate, evidence=[ev(s, i) for i, s in enumerate(LANE_SOURCE.values())]))


async def flow():
    graph = rg.build_graph(llm=object())
    mermaid = graph.get_graph().draw_mermaid()
    assert all(f"{lane} --> anti_hype" in mermaid for lane in LANES) and "anti_hype --> judge" in mermaid
    cfg = {"max_concurrency": rg.LANE_CONCURRENCY}
    rg.evaluate_lane, rg.write_risk_memo, rg.decide = fake_eval, fake_memo, fake_decide

    # four agents really run at the same time (a 4-party barrier would deadlock if they ran one after another)
    gate = asyncio.Barrier(4)

    async def barrier_eval(llm, payload):
        await asyncio.wait_for(gate.wait(), 5)
        return card(payload["lane"])

    rg.evaluate_lane = barrier_eval
    out = await graph.ainvoke(all_lanes(cand()), config=cfg)
    assert sorted(c["lane"] for c in out["scorecards"]) == sorted(LANES) and out["errors"] == []
    assert out["fatal_flaws_found"] is True and out["risk_memo"].startswith("Risk one")
    assert out["verdict"]["category"] == "ASSESS" and out["rule_notes"], "judge node applies the matrix to the model's TRIAL"
    print("OK 4 source agents in parallel, scorecards merged, anti-hype + judge, matrix applied inside the judge node")

    # only sources with data run; the skeptic and the judge run once
    reset(); rg.evaluate_lane = fake_eval
    out = await graph.ainvoke(rg.initial_state(cand("GitHub", "RSS feeds", "RSS feeds")), config=cfg)
    assert sorted(log["eval"]) == ["github", "rss"] and log["memo"] == 1 and len(log["decide"]) == 1
    assert log["decide"][0][0] == ["github", "rss"]
    print("OK partial dispatch: 2 sources -> 2 agent calls, skeptic once, judge once")

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


REAL = (rg.evaluate_lane, rg.write_risk_memo, rg.decide)
asyncio.run(flow())
rg.evaluate_lane, rg.write_risk_memo, rg.decide = REAL   # flow() swapped in fakes


# ------------------------------------------------------------------ the real LLM functions against hostile model output
class FakeChat:
    def __init__(self, replies):
        self.replies, self.seen = replies, []

    def with_structured_output(self, schema, method=None):
        assert method == "json_schema" and schema["title"] in self.replies
        chat = self

        class Runnable:
            async def ainvoke(self, messages):
                chat.seen.append((schema["title"], messages))
                reply = chat.replies[schema["title"]]
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
    assert "fatal flaws found: YES" in judge_user and "Bad. Worse. Worst." in judge_user
    assert "GitHub agent: NO USABLE DATA" in judge_user and "unknown, not good news" in judge_user
    # the skeptic: the model writes the memo, CODE decides fatal_flaws_found (a model once invented a "friction 9")
    chat = FakeChat({"risk_memo": {"memo": " m ", "fatal_flaws_found": True}})   # a model claiming a flaw is ignored
    risk = await rg.write_risk_memo(chat, cand("RSS feeds"), [card("rss")])
    assert risk == {"fatal_flaws_found": False, "memo": "m"}
    assert "Fatal flaws found by code: none." in chat.seen[0][1][1][1]
    chat = FakeChat({"risk_memo": {"memo": "Licence. Unknowns. Clarify."}})   # a model that says nothing about it is overruled
    risk = await rg.write_risk_memo(chat, cand("GitHub"), [card("github", licensing_risk=9), card("rss")])
    assert risk["fatal_flaws_found"] is True
    assert "licensing_risk is 9" in chat.seen[0][1][1][1] and "put them first" in chat.seen[0][1][1][1]
    print("OK real LLM functions: cleaning, errors, polarity/unknown lanes in the prompts")

    # the whole graph with the real nodes and a scripted model: happy path, then a model that times out
    replies = {"scorecard_rss": {"summary": "InfoQ and CNCF report production use.", "enterprise_traction": 8, "maturity": 9,
                                 "confidence": "high"},
               "risk_memo": {"memo": "None. None. None.", "fatal_flaws_found": False},
               "verdict": {"justification": "Mature.", "category": "ADOPT", "confidence": "high", "relevance": "HIGH",
                           "business_value": "Observability for customers."}}
    graph = rg.build_graph(FakeChat(replies))
    res = await rg.rate_candidate(graph, cand("RSS feeds", "RSS feeds"))
    assert res["answer"]["ring"] == "Adopt" and res["rule_notes"] == [] and res["errors"] == []
    assert res["scorecards"][0]["scores"] == {"enterprise_traction": 8, "maturity": 9}
    res = await rg.rate_candidate(rg.build_graph(FakeChat(dict(replies, scorecard_rss=TimeoutError("ollama timed out")))),
                                  cand("RSS feeds"))
    assert res["answer"]["ring"] == "Assess" and res["errors"] == ["Foo / rss: ollama timed out"], res
    print("OK whole graph with the real nodes: happy path, and a model timeout degrades to Assess")


asyncio.run(hostile())
print("ALL GRAPH TESTS PASSED")
