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
streamlit run app.py
```

Ollama must be running (`docker compose up -d` in `C:\hackathon`) with the model you pick in the sidebar,
for example `docker exec -it ollama ollama pull llama3.2:3b` or `... pull qwen3:4b`.

A scan makes about `signals / 8 + technologies` LLM calls (default about 17). Start with fewer signals for a quick test.

## Prompts: one per source

Each source has its own extraction prompt in `prompts/`, written for how that source works:

| File | Used for | What it is tuned for |
| --- | --- | --- |
| `prompts/github.md` | GitHub batches | The repo usually *is* the technology; skip awesome-lists, tutorials, demos |
| `prompts/ycombinator.md` | Y Combinator batches | A startup is a company: only name the technology it is built on |
| `prompts/hackernews.md` | Hacker News batches | Titles only: "Show HN: X" → X; skip opinion and business stories |
| `prompts/rss.md` | Blog/news batches | Skip vendor marketing names; releases count without version |
| `prompts/_rules.md` | Added to every source prompt | What counts as a technology, output rules |
| `prompts/classify.md` | Rating each technology | Rings, how to weigh each source's evidence, bbv context |

Batches never mix sources, so each batch gets its own prompt. Each source also sends the model only its useful
fields (GitHub: language, stars, topics, created date; YC: batch, tags; HN: points, comments, link domain; RSS: site, summary).

Edit the prompts in the app (**🧠 Prompts** tab, with a preview of what the model receives) or directly in the files.
Every run stores a fingerprint of the prompts it used. Tick **Reuse sources fetched in the last hour** in the sidebar
to re-run quickly with a new prompt on the same data.

## Files

| File | What it does |
| --- | --- |
| `app.py` | Streamlit UI |
| `config.py` | All settings: rings, quadrants, bbv context, RSS feeds, seed radar, defaults |
| `services/github_service.py`, `yc_service.py`, `hn_service.py`, `rss_service.py` | Collect signals |
| `services/pipeline.py` | Clean → extract technologies → merge and rank → rate → rules |
| `prompts/*.md` | The LLM prompts (one per source + shared rules + rating) |
| `services/ai_service.py` | Loads prompts, formats items per source, JSON schemas, Ollama client |
| `services/radar_chart.py` | Draws the radar (SVG, no extra library) |
| `services/storage.py` | Saves runs, compares with the previous run |
| `services/export.py` | Markdown and CSV downloads |
| `tests/test_offline.py` | End-to-end test with fake sources and a fake LLM |

## Ideas for the hackathon day

- Replace `SEED_RADAR` and `BBV_CONTEXT` in `config.py` with bbv's real technologies and services.
- Add aliases and generic words in `services/pipeline.py` when the model returns duplicates or noise.
- Compare `llama3.2:3b` and `qwen3:4b` on the same settings using the saved runs.
- Let the team vote on each blip and measure agreement for the pitch.
