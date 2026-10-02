# bbv Technology Radar (updated)

Improved version of the original app in `C:\hackathon\tech-radar-ai` (that folder is unchanged).

## What's new compared with the original

| Area | Original | Updated |
| --- | --- | --- |
| Sources | GitHub, Y Combinator (first 5 matches) | GitHub, Y Combinator (newest batches first), Hacker News, 12 RSS feeds |
| What gets rated | Each repo/startup | **Technologies** found across all signals, merged and ranked by mentions |
| LLM answer | Free JSON, unknown rings disappear | JSON schema with fixed rings and quadrants, temperature 0 |
| Rules | none | Repo younger than 6 months → max Assess; Adopt needs 3+ mentions from 2+ sources |
| bbv context | generic "consulting company" | bbv services and industries in the prompt (edit in `config.py`) |
| UI | Four lists | Round radar, KPI row, filters, Insights charts, table, details, signals, export |
| Prompts | One prompt for everything | One extraction prompt per source, editable in the app |
| Memory | Results lost on every click | Every scan saved in `data/runs`; app opens the last run; "New / Moved in / Moved out" vs the previous run |
| Settings | Hard-coded | Sidebar: Ollama address, model, area, sources, days, number of signals and technologies |
| Export | none | Markdown report, Thoughtworks radar CSV, full JSON |

## Run it (Windows, PowerShell)

```powershell
cd C:\hackathon\tech-radar-ai-updated
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # optional: add a GitHub token
python tests\test_offline.py    # checks the logic without internet or Ollama
python tests\test_graph.py      # checks the rating agents the same way
python tests\test_registry.py   # checks the package registry lookup (fake answers, no internet)
streamlit run app.py
```

Ollama must be running (`docker compose up -d` in `C:\hackathon`) with the model you pick in the sidebar,
for example `docker exec -it ollama ollama pull llama3.2:3b` or `... pull qwen3:4b`.

A scan makes up to `signals / 8 + 7 × technologies` LLM calls (default up to about 89; fewer in practice, because a source
agent only runs when its source has data). Start with fewer signals and technologies for a quick test (e.g. 16 and 5).

## Rating: five source agents, an anti-hype agent and a judge

Each technology is rated by a [LangGraph](https://langchain-ai.github.io/langgraph/) pipeline (`services/radar_graph.py`)
instead of one prompt. It is Map-Reduce: the agents never talk to each other, nothing loops.

```
 GitHub evidence ─────────► GitHub agent ──────────────────┐
 Y Combinator evidence ───► Y Combinator agent ────────────┤  (in parallel)
 Hacker News evidence ────► Hacker News agent ─────────────┤
 RSS evidence ────────────► RSS feeds agent ───────────────┤
 npm/PyPI/Maven/Docker ───► Package registries agent ──────┘
                                     │ scorecards
                                     ▼
                            Anti-hype agent  (reasons to reject, risk memo)
                                     ▼
                            Judge  (decision matrix) ─► ring + justification
```

| Agent | Reads | Scores (0-10) |
| --- | --- | --- |
| GitHub | stars per day, last push, forks, issues, license | `developer_velocity`, `project_health`, `licensing_risk` (computed in code from the license) |
| Hacker News | points, comments, story titles | `community_support`, `developer_friction` |
| RSS feeds | articles from engineering sites | `enterprise_traction`, `maturity` |
| Y Combinator | startups building on it | `market_momentum`, `hype_risk` |
| Package registries | monthly downloads and their trend (npm, PyPI), lifetime pulls and official vs unofficial images (Docker Hub), dependents and releases (npm, PyPI, Maven Central) | `production_usage_score`, `integration_velocity_score` (**1-10**) |

`developer_friction`, `hype_risk` and `licensing_risk` are risks: **higher is worse**. All numbers are computed in Python
(`services/radar_lanes.py`); the model only judges them. The five source agents run in parallel (LangGraph `Send`), and
only for sources that have evidence for that technology. Then the anti-hype agent writes a 3-sentence risk memo, and the
judge picks the ring with `with_structured_output`. `fatal_flaws_found` is **computed by code** from the scorecards (a risk
score of 7+, `project_health` or `maturity` of 2 or less, an archived repository) and the memo is written around those facts:
asked to decide it, the 4B model once invented a score and flagged a healthy repository.

**Decision matrix** (constants at the top of `services/radar_graph.py`, written into the judge prompt *and* enforced in code):

- `licensing_risk` above 7 (AGPL, SSPL, no license...) → **Hold**
- fatal flaws found, or any risk score of 7 or more → at most **Assess**
- **Adopt** needs `maturity` of at least 7 (only the RSS agent gives it; no RSS data → at most **Trial**)
- a score from an agent that reports **low confidence** is weak evidence: a risk score from it does not cap the ring, and a
  maturity score from it cannot justify Adopt. `licensing_risk` is computed by code, so it always counts.

The agents cannot claim more confidence than the data allows (checked in code): e.g. one Hacker News story or one RSS article
is always low, Hacker News and Y Combinator are never high, and `project_health` of a repository under 6 months old is at most 6.

After the judge, the simple rules from before still apply (young repo, mentions). The Details tab shows each agent's
scorecard, the anti-hype memo and the rules that were applied.

### The Package registries agent (fifth source)

Hard production usage instead of social media hype: do people really download it, and does other software depend on it?
This source collects no signals. After the technologies are merged, `pipeline.attach_registry_evidence` looks each one up in
the registries (`services/registry_service.py`, no API keys: the npm downloads API, pypistats.org, Docker Hub, and
ecosyste.ms for dependents and release dates) and adds the result to the technology's evidence. Its system prompt is
`prompts/rate_packages.md`; the two scores are `production_usage_score` and `integration_velocity_score`, 1-10.

- **A name is not proof.** npm has a package called `go`. A package is only used when it can be tied to the technology: it is
  listed in `PACKAGE_MAP` in `config.py` (extend it during the day), or its repository link is the technology's own GitHub
  repository, or it is a Docker official image (`library/<name>`), or a Docker image of that name under the repository's owner.
- **It is not a mention.** `mentions` and `sources` do not change, so Adopt still needs 3+ mentions from 2+ sources.
- **What the registries can tell:** npm (12 months) and PyPI (6 months) give a monthly trend, Docker Hub gives lifetime pulls
  (the only lifetime number that exists), Maven Central publishes no download counts at all, only dependents and releases.
- **Checked in code, like the other agents:** confidence comes from the data (a trend of 3+ months is high, a Docker pull count
  or a dependents count is medium), growth is unknown without two months of downloads, and a package with under 6 months of history
  cannot score above 7 because "sustained" is not proven.
- **Advisory for the judge.** The decision matrix is unchanged: Adopt still needs `maturity`, which only the RSS agent gives.
- **Limits:** pypistats.org answers fast bursts with HTTP 429, so PyPI requests are throttled and retried once; if it still
  refuses, the last month comes from ecosyste.ms and the trend is missing. Results are cached for an hour with the sources.
  A registry that fails is a warning, never an error that stops the scan.

Parallel requests hit your local Ollama: at most `RADAR_TECH_CONCURRENCY × RADAR_LANE_CONCURRENCY` at once (default 1 × 5).
If the model runs out of memory, set `RADAR_LANE_CONCURRENCY=1` in `.env`. Ollama usually serves one request at a time on a
CPU anyway, so the parallel agents queue up there: correct, but not faster.

**Ollama is extremely slow (under 1 token/s)?** On hybrid Intel CPUs (performance + efficiency cores), especially inside Docker
or WSL, llama.cpp's default of "all cores" can be ~100× slower than a smaller thread count. Measured on a Core Ultra 7 in
Docker: 0.17 tokens/s with 16 threads, 17.8 tokens/s with 6. Set `OLLAMA_NUM_THREAD=6` (about your performance-core count)
in `.env`; it is passed on every call, extraction and rating. Check the speed with `docker logs ollama --tail 20` (look for
"tokens per second" in the `eval time` line).

## Prompts: one per source

Each source has its own extraction prompt in `prompts/`, written for how that source works:

| File | Used for | What it is tuned for |
| --- | --- | --- |
| `prompts/github.md` | GitHub batches | The repo usually *is* the technology; skip awesome-lists, tutorials, demos |
| `prompts/ycombinator.md` | Y Combinator batches | A startup is a company: only name the technology it is built on |
| `prompts/hackernews.md` | Hacker News batches | Titles only: "Show HN: X" → X; skip opinion and business stories |
| `prompts/rss.md` | Blog/news batches | Skip vendor marketing names; releases count without version |
| `prompts/_rules.md` | Added to every source prompt | What counts as a technology, output rules |
| `prompts/rate_github.md`, `rate_ycombinator.md`, `rate_hackernews.md`, `rate_rss.md`, `rate_packages.md` | The five source agents | Scoring rubric with anchors for each source |
| `prompts/antihype.md` | Anti-hype agent | Hunts for reasons to reject, writes the risk memo |
| `prompts/judge.md` | Judge | Rings, bbv context, the decision matrix |
| `prompts/classify.md` | Old single rating prompt | No longer used by a scan (kept for `test_ai.py`) |

Batches never mix sources, so each batch gets its own prompt. Each source also sends the model only its useful
fields (GitHub: language, stars, topics, created date; YC: batch, tags; HN: points, comments, link domain; RSS: site, summary).

Edit the prompts in the app (**🧠 Prompts** tab, with a preview of what the model receives) or directly in the files.
Every run stores a fingerprint of the prompts it used. Tick **Reuse sources fetched in the last hour** in the sidebar
to re-run quickly with a new prompt on the same data.

## Files

| File | What it does |
| --- | --- |
| `app.py` | Streamlit UI |
| `config.py` | All settings: rings, quadrants, bbv context, RSS feeds, seed radar, package map, defaults |
| `services/github_service.py`, `yc_service.py`, `hn_service.py`, `rss_service.py` | Collect signals |
| `services/registry_service.py` | Looks a technology up in npm, PyPI, Maven Central and Docker Hub (the fifth source) |
| `services/pipeline.py` | Clean → extract technologies → merge and rank → rate → rules |
| `prompts/*.md` | The LLM prompts (one per source + shared rules + rating) |
| `services/ai_service.py` | Loads prompts, formats items per source, JSON schemas, Ollama client |
| `services/radar_graph.py` | The rating graph: state, parallel source agents, anti-hype, judge, decision matrix |
| `services/radar_lanes.py` | What each source agent sees: metrics computed in code, license risk, package download facts, score schemas |
| `services/radar_chart.py` | Draws the radar (SVG, no extra library) |
| `services/storage.py` | Saves runs, compares with the previous run |
| `services/export.py` | Markdown and CSV downloads |
| `tests/test_offline.py` | End-to-end test with fake sources and a fake LLM |
| `tests/test_graph.py` | Rating agents: facts, decision matrix, parallelism, errors (no Ollama needed) |
| `tests/test_registry.py` | Registry lookup: curated and verified packages, rate limits, failures (fake answers, no internet) |

## Ideas for the hackathon day

- Replace `SEED_RADAR` and `BBV_CONTEXT` in `config.py` with bbv's real technologies and services.
- Add aliases and generic words in `services/pipeline.py` when the model returns duplicates or noise.
- Compare `llama3.2:3b` and `qwen3:4b` on the same settings using the saved runs.
- Let the team vote on each blip and measure agreement for the pitch.
