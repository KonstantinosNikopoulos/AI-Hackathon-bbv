"""Multi-agent rating of ONE technology. Replaces the single classify_technology prompt.

Map-Reduce with LangGraph's Send API. No agent talks to another agent: every node reads the shared
state once and writes its own part. Nothing loops.

    START --Send--> github ------+
            Send--> ycombinator -+
            Send--> hackernews --+--> anti_hype --> judge --> END
            Send--> rss ---------+                    ^
            Send--> packages ----+                    |
    START --(established technology)------------------+

 1. Constants and OverallState (below, radar_lanes.py for the per-source facts, radar_record.py for the facts about the whole technology)
 2. Map:       one agent per source that has data, all in parallel. They return a scorecard (two scores each): a digest of
               their source for the judge. The fifth source, the package registries (npm, PyPI, Maven Central, Docker Hub), is
               looked up per technology by pipeline.attach_registry_evidence before the graph runs, and arrives as evidence.
               An ESTABLISHED technology (years old, with real traction: radar_record.standing) skips the map phase and the
               skeptic and is rated by the single prompt (prompts/classify.md) with the facts measured by code added: the model
               knows it, and on the 29 technologies of eval/gold_radar.json the digests of agents that each see one source cost
               21 points of accuracy (62% with them, 83% without), because a famous retired technology still has popular
               headlines. The map phase is for what the model cannot know: the new, the young and the unproven.
 3. Anti-hype: reads the scorecards, writes a 3-sentence risk memo.
 4. Judge:     reads the facts measured by code, ALL the evidence, the scorecards and the memo, and decides the ring. The one
               who sees the whole picture decides: a source agent only sees its own slice.
 5. Guards:    pipeline.apply_rules enforces the few rules that are facts (radar_record.apply_guards) after the judge.
 6. Streamlit: rate_all / rate_candidate (async), called once per scan from pipeline.run_scan.

The prompts are files in prompts/ (rate_<source>.md, antihype.md, judge.md), editable in the app's Prompts tab.
"""
import asyncio
import operator
import re
from functools import partial
from typing import Annotated, Any, Literal, NotRequired, TypedDict, get_args

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from config import BBV_CONTEXT, GRAPH_LANE_CONCURRENCY, GRAPH_TECH_CONCURRENCY, OLLAMA_NUM_THREAD, RINGS
from services.ai_service import CLASSIFY_SCHEMA, classify_message, classify_system, evidence_lines, load_prompt, thinking_setting
from services.radar_lanes import (CODE_CONFIDENCE_LANES, COMPUTED_NAMES, COMPUTED_SCORES, LANE_SOURCE, LANES, RISK_SCORES,
                                  SCORE_FLOOR, SCORE_MIN, SCORES_BY_LANE, Lane, cap_confidence, clean_scores, lane_facts,
                                  lane_schema)
from services.radar_record import build_record, fatal_flaws, format_record, is_established, matrix_rules  # noqa: F401  (matrix_rules: re-exported)

# ------------------------------------------------------------------ 1. constants
# The four rings as the spec writes them. config.RINGS stays the single source of truth for the app ("Adopt", ...);
# the graph speaks upper case and to_classification converts back at the boundary.
Category = Literal["ADOPT", "TRIAL", "ASSESS", "HOLD"]
RING_BY_CODE = {c: r for c in get_args(Category) for r in RINGS if r.upper() == c}
assert set(RING_BY_CODE) == set(get_args(Category)), "Category and config.RINGS have drifted apart"
RING_ORDER = [r.upper() for r in RINGS]   # best first: ADOPT, TRIAL, ASSESS, HOLD

# Parallelism caps for the local Ollama. In flight at once <= TECH_CONCURRENCY * LANE_CONCURRENCY. See config.py.
LANE_CONCURRENCY = GRAPH_LANE_CONCURRENCY
TECH_CONCURRENCY = GRAPH_TECH_CONCURRENCY


# ------------------------------------------------------------------ 1. state
class Evidence(TypedDict):
    """One signal linked to the technology (what pipeline.merge_candidates keeps). `meta` holds stars, points, ...
    A package registry lookup is evidence too (source "Package registries"); its download numbers are in `meta`."""
    source: str
    title: str
    url: str
    text: str
    date: str
    meta: NotRequired[dict[str, Any]]


class Candidate(TypedDict):
    """One merged technology, exactly what pipeline.merge_candidates returns."""
    name: str
    key: str
    what: str
    quadrant: str
    mentions: int
    sources: list[str]
    signal: int
    youngest_repo_days: int | None
    own_repos: list[str]    # urls of GitHub repositories that ARE the technology (not just mention it)
    evidence: list[Evidence]


class Scorecard(TypedDict):
    """What one source agent appends to the state."""
    lane: Lane
    status: Literal["scored", "error"]
    scores: dict[str, int | None]   # 0-10 (packages: 1-10), see radar_lanes.SCORES_BY_LANE (+ COMPUTED_SCORES); None = unknown
    confidence: Literal["high", "medium", "low"]
    summary: str                    # 1-2 sentences the anti-hype agent and the judge can quote
    metrics: dict[str, Any]         # the facts the agent was shown, e.g. {"repos": [...]}


class RiskMemo(TypedDict):
    """What the anti-hype agent returns."""
    fatal_flaws_found: bool
    memo: str                       # the 3-sentence critical risk memo


class Verdict(TypedDict):
    """What the judge returns. category + justification are the spec; the other three keep the UI fields
    (relevance filter, 'For bbv', confidence) alive without a second LLM call."""
    category: Category
    justification: str
    confidence: Literal["high", "medium", "low"]
    relevance: Literal["HIGH", "MEDIUM", "LOW"]
    business_value: str


class LaneInput(TypedDict):
    """The private payload of one Send. Not part of OverallState."""
    lane: Lane
    candidate: Candidate
    lane_raw: dict[str, Any]        # {"evidence": [...]}; for the packages lane the items are registry lookups, not mentions


class OverallState(TypedDict):
    # input
    candidate: Candidate
    raw: dict[Lane, dict[str, Any]]   # per-lane input; a lane that is not in here has no data and is not dispatched
    #   The five lanes (radar_lanes.Lane): github, ycombinator, hackernews, rss, packages. Adding a source means adding a
    #   lane there, not a field here: raw and scorecards are keyed by lane.
    # phase 1 (map): parallel agents append here; operator.add concatenates instead of overwriting
    scorecards: Annotated[list[Scorecard], operator.add]
    # phase 2 (anti-hype)
    fatal_flaws_found: bool
    risk_memo: str
    # phase 3 (reduce)
    verdict: Verdict | None
    rule_notes: Annotated[list[str], operator.add]   # always empty: the guards run in pipeline.apply_rules, after the graph
    # non-fatal problems (a failed agent); pipeline appends these to result["errors"]
    errors: Annotated[list[str], operator.add]


def initial_state(candidate: Candidate) -> OverallState:
    """Split the candidate's evidence into lanes, one per source."""
    lane_of = {source: lane for lane, source in LANE_SOURCE.items()}
    raw: dict[Lane, dict[str, Any]] = {}
    for e in candidate["evidence"]:
        lane = lane_of.get(e.get("source"))
        if lane:
            raw.setdefault(lane, {"evidence": []})["evidence"].append(e)
    return {"candidate": candidate, "raw": raw, "scorecards": [], "fatal_flaws_found": False, "risk_memo": "",
            "verdict": None, "rule_notes": [], "errors": []}


# ------------------------------------------------------------------ the three LLM steps
async def _ask(llm, schema, system, user) -> dict:
    """One structured call. The schema is sent as Ollama's `format`, so the model can only answer in that shape."""
    answer = await llm.with_structured_output(schema, method="json_schema").ainvoke([("system", system), ("human", user)])
    if not isinstance(answer, dict):
        raise TypeError(f"expected a JSON object from the model, got {type(answer).__name__}")
    return answer


def _confidence(answer) -> str:
    return answer.get("confidence") if answer.get("confidence") in ("high", "medium", "low") else "low"


def format_scorecards(scorecards: list[Scorecard]) -> str:
    """The scorecards as text for the anti-hype agent and the judge. Lanes without a usable scorecard are named as unknown."""
    by_lane = {c["lane"]: c for c in scorecards if c["status"] == "scored"}
    lines = []
    for lane in LANES:
        if lane not in by_lane:
            lines.append(f"- {LANE_SOURCE[lane]} agent: NO USABLE DATA. This is unknown, not good news.")
            continue
        c = by_lane[lane]
        scores = ", ".join(f"{n} {v}" + (" (higher = worse)" if n in RISK_SCORES else "")
                           for n, v in c["scores"].items() if v is not None)
        lines.append(f"- {LANE_SOURCE[lane]} agent [{c['confidence']} confidence]: {scores}. {c['summary']}")
    return "\n".join(lines)


async def evaluate_lane(llm, payload: LaneInput) -> Scorecard:
    lane, candidate = payload["lane"], payload["candidate"]
    facts = lane_facts(lane, candidate, payload["lane_raw"]["evidence"])
    user = f"Technology: {candidate['name']}\nWhat it is: {candidate.get('what', '')}\n\n{facts.text}"
    answer = await _ask(llm, lane_schema(lane), load_prompt(f"rate_{lane}"), user)
    scores = {**clean_scores(answer, SCORES_BY_LANE[lane], facts.limits, SCORE_FLOOR.get(lane, SCORE_MIN)), **facts.computed}
    scores.update({name: None for name in facts.unscored})   # the data cannot support these: unknown, whatever the model said
    if all(v is None for v in scores.values()):
        raise ValueError("the model returned no usable scores")
    # What the prompt asks, checked in code: the model cannot claim more confidence than the data allows.
    # Some lanes are not asked for a confidence at all: the data alone decides it.
    claimed = facts.confidence_cap if lane in CODE_CONFIDENCE_LANES else _confidence(answer)
    return Scorecard(lane=lane, status="scored", scores=scores, confidence=cap_confidence(claimed, facts.confidence_cap),
                     summary=str(answer.get("summary", "")).strip()[:400], metrics=facts.metrics)


# The memo is three named fields that code joins into 3 sentences. With a single free-text "memo" field, qwen3:4b copied the
# three numbered instructions of the prompt word for word for EVERY technology of a real scan, so nothing was ever reviewed.
MEMO_FIELDS = ("biggest_risk", "second_risk_or_unknown", "what_must_be_true")
MEMO_SCHEMA = {
    "title": "risk_memo",
    "type": "object",
    "properties": {name: {"type": "string"} for name in MEMO_FIELDS},
    "required": list(MEMO_FIELDS),
}
ECHO_RUN = 10   # this many words in a row that are also in the prompt: the model copied its instructions


def copies_prompt(text: str, prompt: str, run: int = ECHO_RUN) -> bool:
    """True when `text` repeats `run` or more consecutive words of `prompt`. A real memo about a technology never does."""
    words = lambda s: re.findall(r"[a-z0-9]+", s.lower())  # noqa: E731
    haystack, mine = " ".join(words(prompt)), words(text)
    return any(" ".join(mine[i:i + run]) in haystack for i in range(len(mine) - run + 1))


def invented_scores(memo: str, scorecards: list[Scorecard]) -> list[str]:
    """Claims like "developer_friction 9" in the memo that no scorecard backs: a score that does not exist, or another number.
    qwen3:4b wrote "developer_friction: 9" for a technology without a Hacker News card, and the judge repeated it as a fact.
    Naming a score without a number ("developer_friction is unknown") is fine."""
    have = {name: value for c in scorecards if c["status"] == "scored" for name, value in c["scores"].items() if value is not None}
    every = {name for names in SCORES_BY_LANE.values() for name in names} | COMPUTED_NAMES
    claims = re.findall(r"\b(" + "|".join(sorted(every)) + r")\b[^\w\n]{0,12}(?:is |of |at |was )?(\d+)", memo)
    return [f"{name} {number}" for name, number in claims if have.get(name) != int(number)]


async def write_risk_memo(llm, candidate: Candidate, scorecards: list[Scorecard]) -> RiskMemo:
    """The model fills the three memo fields; `fatal_flaws_found` comes from the record (radar_record.fatal_flaws: an archived
    repository, a license nobody can use), and the model is told its result.
    A memo that copies the prompt or leaves a field empty raises, so the judge is told "not reviewed" instead of reading junk."""
    record = build_record(candidate)
    flaws = fatal_flaws(record)
    checks = ("Fatal flaws found by code (facts, do not dispute them, and put them first in biggest_risk):\n"
              + "\n".join(f"- {f}" for f in flaws)) if flaws else "Fatal flaws found by code: none."
    user = (f"Technology: {candidate['name']}\nWhat it is: {candidate.get('what', '')}\n\n"
            f"Facts measured by code:\n{format_record(record)}\n\n"
            f"Scorecards from the source agents:\n{format_scorecards(scorecards)}\n\n{checks}")
    system = load_prompt("antihype")
    answer = await _ask(llm, MEMO_SCHEMA, system, user)
    parts = [str(answer.get(name, "")).strip() for name in MEMO_FIELDS]
    if not all(parts):
        raise ValueError("the model left a part of the risk memo empty")
    memo = " ".join(p if p[-1] in ".!?" else p + "." for p in parts)
    if copies_prompt(memo, system):
        raise ValueError("the model copied its instructions instead of writing a risk memo")
    if invented := invented_scores(memo, scorecards):
        raise ValueError("the risk memo states scores that no scorecard has: " + ", ".join(invented))
    return RiskMemo(fatal_flaws_found=bool(flaws), memo=memo)


VERDICT_SCHEMA = {
    "title": "verdict",
    "type": "object",
    "properties": {
        "justification": {"type": "string"},
        "category": {"type": "string", "enum": list(get_args(Category))},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "relevance": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "business_value": {"type": "string"},
    },
    "required": ["justification", "category", "confidence", "relevance", "business_value"],
}


def judge_system() -> str:
    return (load_prompt("judge").replace("{bbv_context}", BBV_CONTEXT)
            .replace("{matrix}", "\n".join(f"- {rule}" for rule in matrix_rules())))


def judge_input(candidate: Candidate, scorecards: list[Scorecard], risk: RiskMemo) -> str:
    """What the judge reads: the facts code measured, ALL the evidence (the same lines the single prompt reads), the scorecards as
    digests of each source, and the skeptic's memo."""
    parts = [f"Technology: {candidate['name']}", f"What it is: {candidate.get('what', '')}",
             f"Suggested quadrant: {candidate.get('quadrant', '')}", "",
             "Facts measured by code (true, do not dispute them):", format_record(build_record(candidate)), "",
             "Evidence (every item found about it):", *evidence_lines(candidate), "",
             "Scorecards from the source analysts:", format_scorecards(scorecards)]
    if risk["memo"]:
        parts += ["", f"Skeptic's risk memo (fatal flaws found by code: {'YES' if risk['fatal_flaws_found'] else 'no'}):", risk["memo"]]
    else:
        parts += ["", "Skeptic's risk memo: it did not run, so the risks are unchecked."]
    return "\n".join(parts)


async def decide(llm, candidate: Candidate, scorecards: list[Scorecard], risk: RiskMemo) -> Verdict:
    answer = await _ask(llm, VERDICT_SCHEMA, judge_system(), judge_input(candidate, scorecards, risk))
    return Verdict(
        category=answer.get("category") if answer.get("category") in RING_BY_CODE else "ASSESS",
        justification=str(answer.get("justification", "")).strip(),
        confidence=_confidence(answer),
        relevance=answer.get("relevance") if answer.get("relevance") in ("HIGH", "MEDIUM", "LOW") else "LOW",
        business_value=str(answer.get("business_value", "")).strip())


async def decide_direct(llm, candidate: Candidate) -> Verdict:
    """An established technology: the single prompt (prompts/classify.md) with the facts measured by code added to what it reads."""
    user = classify_message(candidate) + "\n\nFacts measured by code (true, do not dispute them):\n" + format_record(build_record(candidate))
    answer = await _ask(llm, CLASSIFY_SCHEMA, classify_system(), user)
    ring = answer.get("ring") if answer.get("ring") in RING_BY_CODE.values() else "Assess"
    return Verdict(
        category=ring.upper(), justification=str(answer.get("reason", "")).strip(), confidence=_confidence(answer),
        relevance=answer.get("relevance") if answer.get("relevance") in ("HIGH", "MEDIUM", "LOW") else "LOW",
        business_value=str(answer.get("business_value", "")).strip())


# ------------------------------------------------------------------ 2. map phase
def route_to_evaluators(state: OverallState):
    """Conditional edge from START: one Send per source that has data. A source without data is never run,
    so no LLM call is spent on it. Returns "judge" when no source has any data, because an empty list of
    Sends would end the graph without a verdict. The five lanes are radar_lanes.LANES: "packages" only has data
    when the registry lookup tied a package to the technology (many have none), and is skipped like any empty source.
    An established technology goes straight to the judge (see the module docstring)."""
    if is_established(build_record(state["candidate"])):
        return ["judge"]
    sends = [Send(lane, LaneInput(lane=lane, candidate=state["candidate"], lane_raw=raw))
             for lane in LANES if (raw := state["raw"].get(lane))]
    return sends or ["judge"]


async def _run_lane(lane: Lane, payload: LaneInput, llm) -> dict:
    try:
        return {"scorecards": [await evaluate_lane(llm, payload)]}
    except Exception as error:  # one broken agent must not stop the others or the judge
        names = SCORES_BY_LANE[lane] + COMPUTED_SCORES.get(lane, ())
        card = Scorecard(lane=lane, status="error", scores={n: None for n in names}, confidence="low",
                         summary=f"{lane} evaluation failed.", metrics={})
        return {"scorecards": [card], "errors": [f"{payload['candidate']['name']} / {lane}: {error}"]}


# Five named nodes, because each source has its own prompt (prompts/rate_<source>.md) and its own facts.
async def github_node(payload: LaneInput, *, llm) -> dict:
    return await _run_lane("github", payload, llm)


async def ycombinator_node(payload: LaneInput, *, llm) -> dict:
    return await _run_lane("ycombinator", payload, llm)


async def hackernews_node(payload: LaneInput, *, llm) -> dict:
    return await _run_lane("hackernews", payload, llm)


async def rss_node(payload: LaneInput, *, llm) -> dict:
    return await _run_lane("rss", payload, llm)


async def packages_node(payload: LaneInput, *, llm) -> dict:
    """Package registries (npm, PyPI, Maven Central, Docker Hub): hard production usage and integration velocity."""
    return await _run_lane("packages", payload, llm)


# ------------------------------------------------------------------ 3. anti-hype
async def anti_hype_node(state: OverallState, *, llm) -> dict:
    scored = [c for c in state["scorecards"] if c["status"] == "scored"]
    if not scored:
        return {"fatal_flaws_found": False, "risk_memo": ""}  # nothing to attack
    try:
        risk = await write_risk_memo(llm, state["candidate"], scored)
    except Exception as error:
        # Do NOT report "no flaws" as if it had been checked: an empty memo tells the judge it was not reviewed.
        return {"fatal_flaws_found": False, "risk_memo": "",
                "errors": [f"{state['candidate']['name']} / anti_hype: {error}"]}
    return {"fatal_flaws_found": risk["fatal_flaws_found"], "risk_memo": risk["memo"]}


# ------------------------------------------------------------------ 4. judge
async def judge_node(state: OverallState, *, llm) -> dict:
    if is_established(build_record(state["candidate"])):
        return {"verdict": await decide_direct(llm, state["candidate"])}   # no map phase ran: nothing to digest
    scored = [c for c in state["scorecards"] if c["status"] == "scored"]
    if not scored:
        # Mirrors the existing prompt rule "if the evidence is thin, choose Assess with low confidence".
        return {"verdict": Verdict(
            category="ASSESS", confidence="low", relevance="LOW", business_value="",
            justification="No source agent produced a usable score, so there is not enough evidence to place it higher.")}
    risk = RiskMemo(fatal_flaws_found=state["fatal_flaws_found"], memo=state["risk_memo"])
    # An exception here is not caught: pipeline.run_scan already records it and skips the technology. The judge's category is
    # returned as it is: the guards (radar_record.apply_guards) run in pipeline.apply_rules, so the report shows what the model said.
    return {"verdict": await decide(llm, state["candidate"], scored, risk)}


# ------------------------------------------------------------------ wiring
def build_graph(llm):
    """llm: the LangChain chat model (ChatOllama) every node uses. Build once per scan, reuse per technology."""
    builder = StateGraph(OverallState)
    builder.add_node("github", partial(github_node, llm=llm))
    builder.add_node("ycombinator", partial(ycombinator_node, llm=llm))
    builder.add_node("hackernews", partial(hackernews_node, llm=llm))
    builder.add_node("rss", partial(rss_node, llm=llm))
    builder.add_node("packages", partial(packages_node, llm=llm))
    builder.add_node("anti_hype", partial(anti_hype_node, llm=llm))
    builder.add_node("judge", partial(judge_node, llm=llm))

    builder.add_conditional_edges(START, route_to_evaluators, [*LANES, "judge"])
    for lane in LANES:
        builder.add_edge(lane, "anti_hype")  # separate edges, not a list: anti_hype runs once, whichever lanes ran
    builder.add_edge("anti_hype", "judge")
    builder.add_edge("judge", END)
    return builder.compile()


def make_chat_model(host, model, timeout=600):
    """The local model. Build one per scan: its async client belongs to the event loop of that scan."""
    from langchain_ollama import ChatOllama   # imported here so tests with a fake model don't need it
    kwargs = {}
    think = thinking_setting(host, model)
    if think is not None:
        kwargs["reasoning"] = think  # thinking is very slow on CPU (same switch as ai_service.LLM)
    if OLLAMA_NUM_THREAD:
        kwargs["num_thread"] = OLLAMA_NUM_THREAD  # must match ai_service.LLM, or Ollama reloads the model between calls
    return ChatOllama(model=model, base_url=host, temperature=0, num_ctx=8192,
                      client_kwargs={"timeout": timeout}, **kwargs)


# ------------------------------------------------------------------ 5. Streamlit entry points
def to_classification(candidate: Candidate, verdict: Verdict) -> dict:
    """Verdict -> the dict classify_technology returned, so apply_rules and the UI don't change."""
    return {"quadrant": candidate["quadrant"], "ring": RING_BY_CODE[verdict["category"]],
            "summary": candidate.get("what", ""), "reason": verdict["justification"],
            "business_value": verdict.get("business_value", ""), "relevance": verdict.get("relevance", "LOW"),
            "confidence": verdict.get("confidence", "low")}


async def rate_candidate(graph, candidate: Candidate) -> dict:
    """Async drop-in for ai_service.classify_technology. "answer" has the old classify keys."""
    final = await graph.ainvoke(initial_state(candidate), config={"max_concurrency": LANE_CONCURRENCY})
    record = build_record(candidate)
    return {"answer": to_classification(candidate, final["verdict"]), "scorecards": final["scorecards"],
            "fatal_flaws_found": final["fatal_flaws_found"] or bool(fatal_flaws(record)), "risk_memo": final["risk_memo"],
            "rule_notes": final["rule_notes"], "errors": final["errors"], "standing": record["standing"],
            "route": "direct" if is_established(record) else "agents"}


async def rate_all(graph, candidates: list[Candidate], on_done=lambda i, candidate: None) -> list:
    """Rate every candidate, at most TECH_CONCURRENCY at a time. Returns, in order, either the rate_candidate
    dict or the exception for that candidate (so one failure does not lose the others). `on_done(i, candidate)`
    runs on the event loop thread, which in Streamlit is the script thread, so it may call st.progress."""
    gate = asyncio.Semaphore(TECH_CONCURRENCY)

    async def one(i, candidate):
        async with gate:
            try:
                return await rate_candidate(graph, candidate)
            finally:
                on_done(i, candidate)

    return await asyncio.gather(*(one(i, c) for i, c in enumerate(candidates)), return_exceptions=True)
