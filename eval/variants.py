"""Two other ways to rate a technology, for the question "why not break the single prompt into smaller, specific ones?".
They are measured with the same evidence, the same guards and the same gold lists as the app's agents (run_gold.py --mode ...).

  classicfacts  the old single prompt as it is, with the facts measured by code added to what it reads. This is what the app does for an
                ESTABLISHED technology (radar_graph.decide_direct).
  questions     THREE small calls, each asking one specific question about the technology (is it still alive? how widely is it used? how
                mature is it?) with the whole picture in front of it, combined by a table anybody can read. Compare this with the app's
                agents, which split by SOURCE: there every agent sees one slice only.

The three question prompts below were written once, before any result, and not changed afterwards."""
import asyncio

from services import ai_service, radar_graph as rg
from services.ai_service import evidence_lines
from services.radar_record import build_record, format_record

RINGS = ["Adopt", "Trial", "Assess", "Hold"]
KNOWLEDGE = ("Read the facts measured by code (they are true: an archived repository is a retired one) and the dated evidence. For the standing "
             '"established" or "widespread" use what you know about the technology as well; for any other standing you cannot know it from '
             "memory, and its name may belong to something else, so judge only from the facts and the evidence.")
FORMAT = "Write a one-sentence reason first, then the answer. Answer only with JSON that matches the schema."

QUESTIONS = {
    "lifecycle": ("You are an analyst on a technology radar team. You answer ONE question about ONE technology: is it still alive?\n"
                  '- "active": still maintained and still recommended for new work.\n'
                  '- "retired": end of life, deprecated, abandoned or replaced by a successor, so new work should not start on it.\n'
                  + KNOWLEDGE + "\n" + FORMAT, ["active", "retired"]),
    "adoption": ("You are an analyst on a technology radar team. You answer ONE question about ONE technology: how widely is it used in real production?\n"
                 '- "widespread": a standard that very many companies have run in production for years.\n'
                 '- "established": used in production by many companies, but not everywhere.\n'
                 '- "niche": used by some teams or one community.\n'
                 '- "unproven": little or no evidence of real use. Stars and headlines show attention, not use.\n'
                 "Real use shows in package downloads and dependents, image pulls, years of history and releases.\n"
                 + KNOWLEDGE + "\n" + FORMAT, ["widespread", "established", "niche", "unproven"]),
    "maturity": ("You are an analyst on a technology radar team. You answer ONE question about ONE technology: how mature and stable is it?\n"
                 '- "stable": long established, stable releases, no big breaking changes expected.\n'
                 '- "young": usable, but only a few years old or still changing quickly.\n'
                 '- "experimental": new, alpha, beta or preview, or nothing shows that it is stable.\n'
                 + KNOWLEDGE + "\n" + FORMAT, ["stable", "young", "experimental"]),
}
RING_OF_ADOPTION = {"widespread": "Adopt", "established": "Trial", "niche": "Assess", "unproven": "Assess"}
RING_CAP_OF_MATURITY = {"stable": "Adopt", "young": "Trial", "experimental": "Assess"}


def combine(lifecycle, adoption, maturity):
    """The table: retired is Hold; otherwise the ring of how widely it is used, capped by how mature it is."""
    if lifecycle == "retired":
        return "Hold"
    return RINGS[max(RINGS.index(RING_OF_ADOPTION[adoption]), RINGS.index(RING_CAP_OF_MATURITY[maturity]))]


def picture(candidate):
    """What every question and the single judge read: the facts measured by code and all the evidence, no scorecards."""
    record = build_record(candidate)
    return "\n".join([f"Technology: {candidate['name']}", f"What it is: {candidate.get('what', '')}", "",
                      "Facts measured by code (true, do not dispute them):", format_record(record), "",
                      "Evidence:", *evidence_lines(candidate)])


def question_schema(name, options):
    return {"title": f"question_{name}", "type": "object",
            "properties": {"reason": {"type": "string"}, "answer": {"type": "string", "enum": options}}, "required": ["reason", "answer"]}


def _rating(candidate, ring, reason, confidence="medium"):
    answer = {"quadrant": candidate["quadrant"], "ring": ring, "summary": candidate.get("what", ""), "reason": reason, "business_value": "",
              "relevance": "LOW", "confidence": confidence}
    return {"answer": answer, "rule_notes": [], "scorecards": [], "errors": [], "risk_memo": ""}


async def _classic_facts(llm, candidate):
    user = ai_service.classify_message(candidate) + "\n\nFacts measured by code (true, do not dispute them):\n" + format_record(build_record(candidate))
    answer = await rg._ask(llm, ai_service.CLASSIFY_SCHEMA, ai_service.classify_system(), user)
    ring = answer.get("ring") if answer.get("ring") in RINGS else "Assess"
    return _rating(candidate, ring, str(answer.get("reason", "")), answer.get("confidence", "low"))


async def _questions(llm, candidate):
    user = picture(candidate)
    names = list(QUESTIONS)
    replies = await asyncio.gather(*(rg._ask(llm, question_schema(n, QUESTIONS[n][1]), QUESTIONS[n][0], user) for n in names))
    answers = {n: r.get("answer") for n, r in zip(names, replies)}
    for n in names:
        if answers[n] not in QUESTIONS[n][1]:
            raise ValueError(f"the model gave no valid answer to the {n} question")
    ring = combine(answers["lifecycle"], answers["adoption"], answers["maturity"])
    return _rating(candidate, ring, "; ".join(f"{n} {answers[n]}" for n in names))


def rate_all(candidates, llm, which, parallel=2):
    """Rate every candidate. Returns, in order, the rating dict or the exception (like radar_graph.rate_all)."""
    one = {"questions": _questions, "classicfacts": _classic_facts}[which]

    async def run():
        gate = asyncio.Semaphore(parallel)

        async def guarded(candidate):
            async with gate:
                return await one(llm, candidate)

        return await asyncio.gather(*(guarded(c) for c in candidates), return_exceptions=True)

    return asyncio.run(run())
