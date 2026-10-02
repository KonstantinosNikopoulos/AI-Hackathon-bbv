# bbv Technology Radar (updated)

Improved version of the original app in `C:\hackathon\tech-radar-ai` (that folder is unchanged).

## What's new compared with the original

| Area | Original | Updated |
| --- | --- | --- |
| Sources | GitHub, Y Combinator (first 5 matches) | GitHub, Y Combinator (newest batches first), Hacker News, 12 RSS feeds |
| What gets rated | Each repo/startup | **Technologies** found across all signals, merged and ranked by mentions |
| LLM answer | Free JSON, unknown rings disappear | JSON schema with fixed rings and quadrants, temperature 0 |
| Rules | none | Facts only: archived repository or unusable license → Hold; younger than 6 months → max Assess. Nothing stops a well-known technology from being Adopt (see Rating) |
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
python tests\test_record.py     # checks the facts, the standing and the rules
python tests\test_registry.py   # checks the package registry lookup (fake answers, no internet)
python tests\test_gold.py       # checks the gold-set harness (fake answers, no internet)
streamlit run app.py
```

Ollama must be running (`docker compose up -d` in `C:\hackathon`) with the model you pick in the sidebar,
for example `docker exec -it ollama ollama pull llama3.2:3b` or `... pull qwen3:4b`.

**Look back** is any number of days (1 to 36,500; type 365 or 3650 to include older projects). GitHub lists repositories created
in that window, most starred first; Hacker News lists the most popular stories of the window (Algolia's points-ranked
search: the newest-first search used before returned only the last few days whatever the look-back); RSS feeds only list
their newest items, so a long look-back adds little there.

A scan makes `signals / 8 + technologies` LLM calls (default about 17: extraction batches plus one rating call per technology). Start with fewer signals and technologies for a quick test (e.g. 16 and 5).

## Rating: one prompt per technology, then rules in code

Each technology is rated by **one prompt** (`prompts/classify.md`) that reads everything collected about it: every dated evidence
line from GitHub, Y Combinator, Hacker News, the RSS feeds and the package registries. It answers with the ring, a reason, the
value for bbv and a relevance. Then **rules in code** (`services/radar_record.py`, applied by `pipeline.apply_rules`) check the
facts a model cannot be trusted with, and every change is shown in the Details tab as a "Rule applied" note.

**The rules** (the only ones; none of them can stop a well-known technology from being Adopt):

- `licensing_risk` above 7 (AGPL, SSPL...) on its own repository → **Hold**
- its own repository is **archived** → **Hold**
- first seen less than 6 months ago (its main repository or a verified package) → at most **Assess**

**Standing** is measured, never guessed by the model, and only **new** changes a ring; the rest is shown in the Details tab and used by
the test harness. It says how old the technology is (from its biggest own repository and its verified packages; stories and articles
can only prove that it is *old*, never that it is new, because a scan only sees recent items) and how much it is used. **new** (under 6 months) · **emerging** (under 3 years, or older with no traction) · **established** (3+ years, and a
repository with 1,000+ stars, a package with 100,000+ downloads a month, an image with 10M+ pulls or 1,000+ dependents; with no repository
at all, 3 or more stories or articles about it) ·
**widespread** (established, and ten times that: 10,000+ stars, 1M+ downloads a month, 100M+ pulls, 10,000+ dependents) · **unknown**
(nothing shows its age). The numbers are at the top of `services/radar_record.py`.

**Not rules any more:** scores that a 4B model guessed from headlines (developer friction, hype risk, maturity) and the number of
mentions. A first version made those vetoes (five source agents, an anti-hype agent and a judge) and on the 29 technologies of
`eval/gold_radar.json` it scored 48% against 83% for the single prompt, with 0 of 9 Adopt right: headlines made Linux, PostgreSQL
and Kubernetes look "frictioned". Splitting the prompt into smaller ones lost accuracy every time it was measured (see "Is the model
right?" below), so the scan went back to one prompt. That code is in the git history (commit `55b63cf`).

**Why there is no "Adopt needs scale" rule.** One was tried ("Adopt only for a technology 3+ years old with 10,000+ stars, 1M+ downloads a
month, 100M+ pulls or 10,000+ dependents, else Trial"). On the gold list it raised the exact accuracy from 79% to 90%, because it
caught CNCF *incubating* projects (KubeVirt, Strimzi, Longhorn, OpenFeature) that the model called Adopt. But a scan often cannot see a
technology's age or size, so it also capped famous technologies such as Git and Spring at Trial, and a small but ubiquitous library
with few stars would be capped too. The goal is that the most known and used technologies are rated Adopt, so the rule was removed.

### Package registries (fifth source)

Hard production usage instead of social media hype: do people really download it, and does other software depend on it?
This source collects no signals. After the technologies are merged, `pipeline.attach_registry_evidence` looks each one up in
the registries (`services/registry_service.py`, no API keys: the npm downloads API, pypistats.org, Docker Hub, and
ecosyste.ms for dependents and release dates) and adds the result to the technology's evidence, where the prompt reads it as one
more dated line ("Package registries: PyPI: ty - 180,000 downloads in the last 30 days, ...") and the rules use it for the standing.

- **A name is not proof.** npm has a package called `go`. A package is only used when it can be tied to the technology: it is
  listed in `PACKAGE_MAP` in `config.py` (extend it during the day), or its repository link is the technology's own GitHub
  repository, or it is a Docker official image (`library/<name>`), or a Docker image of that name under the repository's owner.
- **It is not a mention.** `mentions` and `sources` do not change: a download count is a measurement, not an article.
- **What the registries can tell:** npm (12 months) and PyPI (6 months) give a monthly trend, Docker Hub gives lifetime pulls
  (the only lifetime number that exists), Maven Central publishes no download counts at all, only dependents and releases.
- **Limits:** pypistats.org answers fast bursts with HTTP 429, so PyPI requests are throttled and retried once; if it still
  refuses, the last month comes from ecosyste.ms and the trend is missing. Results are cached for an hour with the sources.
  A registry that fails is a warning, never an error that stops the scan.

**Ollama is extremely slow (under 1 token/s)?** On hybrid Intel CPUs (performance + efficiency cores), especially inside Docker
or WSL, llama.cpp's default of "all cores" can be ~100× slower than a smaller thread count. Measured on a Core Ultra 7 in
Docker: 0.17 tokens/s with 16 threads, 17.8 tokens/s with 6. Set `OLLAMA_NUM_THREAD=6` (about your performance-core count)
in `.env`; it is passed on every call, extraction and rating. Check the speed with `docker logs ollama --tail 20` (look for
"tokens per second" in the `eval time` line).

## Is the model right? Known technologies with a certain ring

`eval/gold_radar.json` lists 29 technologies whose ring is not a matter of opinion, each with the reason, a source and a `check`
that can be re-tested against a live source (`python eval/run_gold.py --verify`):

| Gold ring | Why it is certain | Technologies |
| --- | --- | --- |
| **Hold** (8) | The vendor or maintainers ended support: an official end-of-life date or an archived repository | Python 2, AngularJS, TSLint, Xamarin.Forms, PhoneGap, Internet Explorer 11, Adobe Flash Player, Microsoft Silverlight |
| **Adopt** (9) | Universal, measurable and still maintained: Docker pulls in the billions, npm downloads in the hundreds of millions, CNCF *graduated* | Linux, Git, PostgreSQL, Python, Kubernetes, Prometheus, TypeScript, React, Continuous integration |
| **Trial** (6) | CNCF maturity level *incubating* | Backstage, Thanos, KubeVirt, Strimzi, Longhorn, OpenFeature |
| **Assess** (6) | CNCF maturity level *sandbox* | Headlamp, Kepler, Inspektor Gadget, Kubewarden, Tinkerbell, Akri |

Hold and Adopt are facts. Trial and Assess can only be certain against a stated rubric, so they use the public CNCF ladder
(Graduated = Adopt, Incubating = Trial, Sandbox = Assess, Archived = Hold). If bbv has a radar of its own, add its entries to the file
with `"basis": "bbv"` and test against that instead.

**Two lists, so that the rules are not just fitted to the list they were designed on.** The 29 technologies above are what the rules
were diagnosed and designed on, so a number on them is optimistic by construction. `eval/heldout_radar.json` holds 43 technologies that
nobody looked at while the rules were designed. `eval/build_heldout.py` draws them with a fixed seed (2026): up to 8 random CNCF projects per
maturity level (Graduated with 10,000+ stars = Adopt, Incubating with 1,000+ = Trial, Sandbox with 100+ = Assess, Archived = Hold), plus
12 outside cloud native, each with a check (Java, Node.js, Docker, nginx, Go and Spring Boot = Adopt; Dockershim, PodSecurityPolicy,
Helm 2, CentOS Linux, PHP 5 and Apache Struts 1 = Hold). The rules were frozen before the first held-out run.

**Results** (qwen3:4b, the same evidence for every row, exact ring):

| How the ring is decided | gold, 29 (designed on) | held-out, 43 (checked on) |
| --- | --- | --- |
| The single prompt, with the first rules (mentions, RSS maturity) | 24/29 = 83% | 24/43 = 56% |
| **The single prompt + the facts-only rules (what the app does)** | **23/29 = 79%** | **32/43 = 74%** |
| The single prompt alone, no rules at all | 23/29 = 79% | 27/43 = 63% |
| The same + a rule "Adopt needs scale" (removed, see above) | 26/29 = 90% | 34/43 = 79% |
| The single prompt plus the facts written into the prompt | 25/29 = 86% | not run |
| Three small questions (alive? used? mature?) and a table | 22/29 = 76% | not run |
| Five source agents + anti-hype + judge, new judge | 18/29 = 62% | not run |
| Five source agents + anti-hype + judge, first rules | 14/29 = 48% | not run |

**Adopt, the ring that matters most: every technology whose gold ring is Adopt is found (9 of 9 on the gold list, 14 of 14 on the
held-out list).** The misses are on the middle of the ladder: the model calls CNCF *incubating* projects (gold Trial) Adopt or Assess,
0 of 6 and 0 of 8 right, because incubating against graduated is a committee's decision that a repository does not show. Wrongly
Adopt: 5 of 29 and 5 of 43. The facts-only rules add 11 points on the held-out list (Hold 6/13 → 11/13: archived repositories) and
nothing on the gold list.

Reading it: **more pieces, less accuracy.** The single prompt sees all the evidence at once and can use what the model knows (that
Flash and Python 2 are retired); an agent that sees one source cannot, and a small model believes the confident digests it is
handed. The rows with the "Adopt needs scale" rule are shown for comparison; it was added after the first results on the gold list,
so its 90% is optimistic. A later design that sent only established technologies to the single prompt and the rest to the agents (not
in the table; removed) scored 34/43 = 79% on the held-out list, so the extra code earned nothing. The held-out runs of the other
variants were stopped unfinished.

```powershell
python eval/run_gold.py --model qwen3:4b --host http://localhost:11435   # all 29, the way the app rates
python eval/run_gold.py --gold eval/heldout_radar.json                    # the held-out 43
python eval/run_gold.py --limit 2                                         # 2 per ring: a quick try
python eval/run_gold.py --rescore data/eval/results/<file>.json           # the same model answers, the current rules: free
python eval/run_gold.py --compare                                         # every saved result side by side, per technology
```

For each technology it collects **real evidence by name** once (its GitHub repository, Hacker News stories, press articles from The New
Stack, GitHub, .NET and CNCF blogs, and curated registry data) and saves it in `data/eval/evidence`, so every model is shown exactly
the same facts. It then rates like the app (the single prompt, then the rules) and prints the confusion matrix, the accuracy per ring
and per standing, which sources had data for every miss, and the rule notes. Results are saved in `data/eval/results`
(`--rules v4` labels a run so that `--compare` keeps it apart). GitHub's anonymous limit is 60 requests an hour: put a `GITHUB_TOKEN` in
`.env` before collecting evidence for a long list.

**Limits of these numbers.** The harness is *given* each technology's own repository (it is in the list), so it measures the rules
and the model when the facts are known. A real scan only knows repositories that are named like the technology among its trending GitHub
results, so most of its technologies have the standing "unknown" (Adopt capped at Trial) until their repository is found. Looking a
repository up by name is possible, but a name can belong to something else (GitHub's biggest repository named "Python" is a list of
algorithms), and a wrong "established" would relax the caps, so it is not done. Trial against Adopt is the hard
part for a 4B model: CNCF *incubating* and *graduated* are decisions of a committee, not something a repository shows.

## Prompts: one per source

Each source has its own extraction prompt in `prompts/`, written for how that source works:

| File | Used for | What it is tuned for |
| --- | --- | --- |
| `prompts/github.md` | GitHub batches | The repo usually *is* the technology; skip awesome-lists, tutorials, demos |
| `prompts/ycombinator.md` | Y Combinator batches | A startup is a company: only name the technology it is built on |
| `prompts/hackernews.md` | Hacker News batches | Titles only: "Show HN: X" → X; skip opinion and business stories |
| `prompts/rss.md` | Blog/news batches | Skip vendor marketing names; releases count without version |
| `prompts/_rules.md` | Added to every source prompt | What counts as a technology, output rules |
| `prompts/classify.md` | The rating prompt | Rates one technology in one call from all its evidence: rings, how to weigh GitHub, Hacker News, RSS and Y Combinator evidence, bbv context |

Batches never mix sources, so each batch gets its own prompt. Each source also sends the model only its useful
fields (GitHub: language, stars, topics, created date; YC: batch, tags; HN: points, comments, link domain; RSS: site, summary).

Edit the prompts in the app (**🧠 Prompts** tab, with a preview of what the model receives) or directly in the files.
Every run stores a fingerprint of the prompts it used. Tick **Reuse sources fetched in the last hour** in the sidebar
to re-run quickly with a new prompt on the same data.

## Files

| File | What it does |
| --- | --- |
| `app.py` | Streamlit UI |
| `config.py` | All settings: rings, quadrants, bbv context, RSS feeds, package map, defaults |
| `services/github_service.py`, `yc_service.py`, `hn_service.py`, `rss_service.py` | Collect signals |
| `services/registry_service.py` | Looks a technology up in npm, PyPI, Maven Central and Docker Hub (the fifth source) |
| `services/pipeline.py` | Clean → extract technologies → merge and rank → rate → rules |
| `prompts/*.md` | The LLM prompts (one per source + shared rules + the rating prompt) |
| `services/ai_service.py` | Loads prompts, formats items per source, JSON schemas, Ollama client |
| `services/radar_record.py` | The rules in code: facts about a technology (age, traction, archived, license), its standing, and the guards |
| `services/radar_chart.py` | Draws the radar (SVG, no extra library) |
| `services/storage.py` | Saves runs, compares with the previous run |
| `services/export.py` | Markdown and CSV downloads |
| `tests/test_offline.py` | End-to-end test with fake sources and a fake LLM |
| `tests/test_record.py` | Facts, standing and rules: age tied to the technology, scale, archived, license (no Ollama needed) |
| `eval/gold_radar.json`, `eval/heldout_radar.json`, `eval/run_gold.py` | Known technologies with a certain ring (the list the rules were designed on, and the held-out list), and the harness that tests a model against them |
| `eval/build_heldout.py` | Draws the held-out list by rules and a fixed seed |
| `tests/test_gold.py` | The gold-set harness: collectors, rating, report, label checks (no internet needed) |
| `tests/test_registry.py` | Registry lookup: curated and verified packages, rate limits, failures (fake answers, no internet) |

## Ideas for the hackathon day

- Replace `BBV_CONTEXT` in `config.py` with bbv's real services and industries.
- Add aliases and generic words in `services/pipeline.py` when the model returns duplicates or noise.
- Compare `llama3.2:3b` and `qwen3:4b` on the same settings using the saved runs.
- Let the team vote on each blip and measure agreement for the pitch.
