# bbv Technology Radar (updated)

Improved version of the original app in `C:\hackathon\tech-radar-ai` (that folder is unchanged).

## What's new compared with the original

| Area | Original | Updated |
| --- | --- | --- |
| Sources | GitHub, Y Combinator (first 5 matches) | GitHub, Y Combinator (newest batches first), Hacker News, 12 RSS feeds |
| What gets rated | Each repo/startup | **Technologies** found across all signals, merged and ranked by mentions |
| LLM answer | Free JSON, unknown rings disappear | JSON schema with fixed rings and quadrants, temperature 0 |
| Rules | none | Written into the rating prompt: archived repository or unusable license → Hold; younger than 6 months → max Assess; a well-known standard → Adopt (see Rating) |
| bbv context | generic "consulting company" | bbv services and industries in the prompt (edit in `config.py`) |
| UI | Four lists | Round radar, KPI row, filters, Insights charts, table, details, signals, export |
| Prompts | One prompt for everything | One prompt for the whole app (`[EXTRACTION]` + `[RATING]` sections), editable in the app |
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
python tests\test_record.py     # checks the facts the rating prompt reads
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

## Rating: one prompt per technology, with the rules inside it

Each technology is rated by **one prompt** (the `[RATING]` section of `prompts/prompt.md`) that reads everything collected about it: every dated evidence
line from GitHub, Y Combinator, Hacker News, the RSS feeds and the package registries, and a few **facts measured by code** about
its own GitHub repository (age in days, stars, last push, license, archived or not), because a model cannot count days or read a
license id reliably. It answers with the ring, a reason, the value for bbv and a relevance. **The rules are in the prompt, in words**,
and nothing in code changes the ring afterwards (`pipeline.apply_rules` only checks that the answer is a ring of the radar):

- its own repository is **archived**, or its license blocks commercial reuse (AGPL, SSPL, BUSL: the facts say "HIGH licensing risk"),
  or it is retired or replaced → **Hold**
- created in the last 6 months → at most **Assess**
- a technology the model knows to be a standard or the default choice, run in production by very many companies for years →
  **Adopt**, even when the evidence shown is thin; nothing else stops Adopt (no minimum of stars or articles)
- a technology it does not know, or whose name may belong to something else: **Adopt** only if the evidence clearly shows broad, mature
  production use; thin evidence → **Assess** with low confidence

Edit the prompt in the app (**🧠 Prompts** tab) to change a rule. History: a first version made vetoes out of scores that a 4B model
guessed from headlines (five source agents, an anti-hype agent and a judge: 48% on the 29 known technologies, 0 of 9 Adopt right, because
headlines made Linux and Kubernetes look "frictioned"), and a second kept the single prompt but changed its ring in code afterwards. Both
are in the git history (commits `55b63cf` and `5e87711`). One code rule, "Adopt needs years of use at scale", raised the accuracy on the
gold list from 79% to 90% but capped famous technologies whose age a scan cannot see (Git, Spring), so it was dropped.

### Package registries (fifth source)

Hard production usage instead of social media hype: do people really download it, and does other software depend on it?
This source collects no signals. After the technologies are merged, `pipeline.attach_registry_evidence` looks each one up in
the registries (`services/registry_service.py`, no API keys: the npm downloads API, pypistats.org, Docker Hub, and
ecosyste.ms for dependents and release dates) and adds the result to the technology's evidence, where the prompt reads it as one
more dated line ("Package registries: PyPI: ty - 180,000 downloads in the last 30 days, ...") so the prompt sees what is really downloaded.

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
| The single prompt, with the first rules in code (mentions, RSS maturity) | 24/29 = 83% | 24/43 = 56% |
| The single prompt as it was, no rules at all | 23/29 = 79% | 27/43 = 63% |
| The same + rules in code: archived / license → Hold | 23/29 = 79% | 32/43 = 74% |
| The same + a code rule "Adopt needs scale" (dropped, see above) | 26/29 = 90% | 34/43 = 79% |
| **The single prompt with the rules inside it, and the facts (what the app does)** | **20/29 = 69%** | **34/43 = 79%** |
| Three small questions (alive? used? mature?) and a table | 22/29 = 76% | not run |
| Five source agents + anti-hype + judge | 18/29 = 62% (48% with the first rules) | not run |

**Adopt and Hold are right on both lists with the rules inside the prompt: every Adopt (9 of 9 and 14 of 14) and every Hold (8 of 8
and 13 of 13).** Archived repositories went from 6 of 13 Hold (no rules) to 13 of 13. The weak part is the middle of the ladder: CNCF
*incubating* projects (gold Trial) are called Adopt or Assess, 0 of 6 and 1 of 8 right, because incubating against graduated is a
committee's decision that a repository does not show; and on the gold list 3 of the 6 *sandbox* projects (gold Assess) were called
Adopt, which is why it scores 10 points below the held-out list. The gold list is the one the rules were designed on, so these are
honest numbers for the final prompt, not tuned ones: the wording was written once and measured once.

Reading it: **more pieces, less accuracy.** The single prompt sees all the evidence at once and can use what the model knows (that
Flash and Python 2 are retired); an agent that sees one source cannot, and a small model believes the confident digests it is
handed. The rows above the bold one were scored with the earlier code rules and the original prompt text.

**Limits of these numbers.** The test harness collects evidence by name for every technology (its repository, the most popular Hacker
News stories and press articles about it, curated registry data: 3 to 8 evidence lines). A real scan only sees what its sources
returned inside the look-back window, usually 1 or 2 lines per technology and no registry numbers, so a scan gives the model less to
go on than these tests did and rates more technologies Assess.

```powershell
python eval/run_gold.py --model qwen3:4b --host http://localhost:11435   # all 29, the way the app rates
python eval/run_gold.py --gold eval/heldout_radar.json                    # the held-out 43
python eval/run_gold.py --limit 2                                         # 2 per ring: a quick try
python eval/run_gold.py --compare                                         # every saved result side by side, per technology
```

For each technology it collects **real evidence by name** once (its GitHub repository, Hacker News stories, press articles from The New
Stack, GitHub, .NET and CNCF blogs, and curated registry data) and saves it in `data/eval/evidence`, so every model is shown exactly
the same facts. It then rates like the app (the single prompt, which holds the rules) and prints the confusion matrix, the accuracy per
ring, and which sources had data for every miss. Results are saved in `data/eval/results`
(`--rules v4` labels a run so that `--compare` keeps it apart). GitHub's anonymous limit is 60 requests an hour: put a `GITHUB_TOKEN` in
`.env` before collecting evidence for a long list.

## Prompt: one for the whole app

The app uses **one prompt**, `prompts/prompt.md`, edited in the app (**🧠 Prompts** tab) or directly in the file. It has two
sections, each starting with its marker on a line of its own:

| Section | Used for | What it holds |
| --- | --- | --- |
| `[EXTRACTION]` | Every extraction batch, all sources | What counts as a technology, how to read GitHub, Y Combinator, Hacker News and RSS items, output rules |
| `[RATING]` | Every rating call (one per technology) | Rates one technology from all its evidence and the facts measured by code; holds the rules (Hold, too new, Adopt). `{bbv_context}` is replaced with the text in `config.py` |

Batches never mix sources and the user message names the source ("Items from GitHub:"), so the one extraction section can
describe every source. Each source also sends the model only its useful fields (GitHub: language, stars, topics, created date;
YC: batch, tags; HN: points, comments, link domain; RSS: site, summary). The app refuses to save a prompt without both markers.

Every run stores a fingerprint of the prompt it used. Tick **Reuse sources fetched in the last hour** in the sidebar
to re-run quickly with a new prompt on the same data.

## Files

| File | What it does |
| --- | --- |
| `app.py` | Streamlit UI |
| `config.py` | All settings: rings, quadrants, bbv context, RSS feeds, package map, defaults |
| `services/github_service.py`, `yc_service.py`, `hn_service.py`, `rss_service.py` | Collect signals |
| `services/registry_service.py` | Looks a technology up in npm, PyPI, Maven Central and Docker Hub (the fifth source) |
| `services/pipeline.py` | Clean → extract technologies → merge and rank → rate → rules |
| `prompts/prompt.md` | The single LLM prompt (`[EXTRACTION]` and `[RATING]` sections) |
| `services/ai_service.py` | Loads prompts, formats items per source, JSON schemas, Ollama client |
| `services/radar_record.py` | The facts measured by code that the rating prompt reads: age, stars, last push, license, archived |
| `services/radar_chart.py` | Draws the radar (SVG, no extra library) |
| `services/storage.py` | Saves runs, compares with the previous run |
| `services/export.py` | Markdown and CSV downloads |
| `tests/test_offline.py` | End-to-end test with fake sources and a fake LLM |
| `tests/test_record.py` | The facts and the rules in the prompt: age, license, archived, nothing changes a ring in code (no Ollama needed) |
| `eval/gold_radar.json`, `eval/heldout_radar.json`, `eval/run_gold.py` | Known technologies with a certain ring (the list the rules were designed on, and the held-out list), and the harness that tests a model against them |
| `eval/build_heldout.py` | Draws the held-out list by rules and a fixed seed |
| `tests/test_gold.py` | The gold-set harness: collectors, rating, report, label checks (no internet needed) |
| `tests/test_registry.py` | Registry lookup: curated and verified packages, rate limits, failures (fake answers, no internet) |

## Ideas for the hackathon day

- Replace `BBV_CONTEXT` in `config.py` with bbv's real services and industries.
- Add aliases and generic words in `services/pipeline.py` when the model returns duplicates or noise.
- Compare `llama3.2:3b` and `qwen3:4b` on the same settings using the saved runs.
- Let the team vote on each blip and measure agreement for the pitch.
