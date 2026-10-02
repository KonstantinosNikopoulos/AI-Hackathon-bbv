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

## Files

| File | What it does |
| --- | --- |
| `app.py` | Streamlit UI |
| `config.py` | All settings: rings, quadrants, bbv context, RSS feeds, seed radar, defaults |
| `services/github_service.py`, `yc_service.py`, `hn_service.py`, `rss_service.py` | Collect signals |
| `services/pipeline.py` | Clean → extract technologies → merge and rank → rate → rules |
| `services/ai_service.py` | Prompts, JSON schemas, Ollama client |
| `services/radar_chart.py` | Draws the radar (SVG, no extra library) |
| `services/storage.py` | Saves runs, compares with the previous run |
| `services/export.py` | Markdown and CSV downloads |
| `tests/test_offline.py` | End-to-end test with fake sources and a fake LLM |

## Ideas for the hackathon day

- Replace `SEED_RADAR` and `BBV_CONTEXT` in `config.py` with bbv's real technologies and services.
- Add aliases and generic words in `services/pipeline.py` when the model returns duplicates or noise.
- Compare `llama3.2:3b` and `qwen3:4b` on the same settings using the saved runs.
- Let the team vote on each blip and measure agreement for the pitch.
