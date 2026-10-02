"""bbv Technology Radar: improved version of the original app (C:\\hackathon\\tech-radar-ai)."""
import json
import math

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from config import (AREAS, BATCH_SIZE, DEFAULT_DAYS, DEFAULT_MAX_SIGNALS, DEFAULT_MODEL, DEFAULT_OLLAMA_HOST,
                    DEFAULT_TOP_N, MAX_DAYS, QUADRANTS, REGISTRY_SOURCE, RINGS, SOURCES, SUGGESTED_MODELS)
from services import export, pipeline, storage
from services import ai_service
from services.ai_service import LLM, SOURCE_PROMPTS
from services.radar_chart import QUADRANT_STYLE, radar_html

st.set_page_config(page_title="bbv Technology Radar", page_icon="📡", layout="wide")

QCOLORS = [QUADRANT_STYLE[q]["light"] for q in QUADRANTS]
RING_ICON = {"Adopt": "🟢", "Trial": "🔵", "Assess": "🟡", "Hold": "⚪"}
STATUS_ICON = {"New": "✨ New", "Moved In": "⬆️ Moved in", "Moved Out": "⬇️ Moved out", "No Change": ""}


@st.cache_data(ttl=30, show_spinner=False)
def models_at(host):
    try:
        return LLM(host, DEFAULT_MODEL, timeout=5).available_models()
    except Exception:
        return None


def swatch(quadrant):
    color = QUADRANT_STYLE.get(quadrant, {}).get("light", "#888")
    return (f'<span style="display:inline-block;width:11px;height:11px;border-radius:3px;'
            f'background:{color};margin-right:6px;vertical-align:-1px"></span>')


PROMPT_LABELS = {
    "GitHub": "github", "Y Combinator": "ycombinator", "Hacker News": "hackernews", "RSS feeds": "rss",
    "Shared rules (added to every source prompt)": "_rules",
    "Rating agent: GitHub": "rate_github", "Rating agent: Y Combinator": "rate_ycombinator",
    "Rating agent: Hacker News": "rate_hackernews", "Rating agent: RSS feeds": "rate_rss",
    "Rating agent: Package registries (npm, PyPI, Maven Central, Docker Hub)": "rate_packages",
    "Anti-hype agent (looks for reasons to reject)": "antihype", "Judge (decision matrix, picks the ring)": "judge",
    "Classic rating prompt (single prompt, not used by the agents)": "classify",
}


def prompt_editor(signals):
    st.write("Each source has its own **extraction prompt**, tuned to how that source works. The shared rules are "
             "added to every source prompt. Rating is done by **agents**: one per source scores the evidence, an "
             "anti-hype agent looks for reasons to reject, and a judge picks the ring. Saved changes apply to the next scan.")
    choice = st.selectbox("Prompt", list(PROMPT_LABELS), key="prompt_choice")
    name = PROMPT_LABELS[choice]
    text = st.text_area("Prompt text", ai_service.load_prompt(name), height=360, key=f"prompt_text_{name}")
    if st.button("💾 Save prompt", key=f"prompt_save_{name}"):
        ai_service.save_prompt(name, text)
        st.success(f"Saved prompts/{name}.md. Run a new scan to use it (tick 'Reuse sources' to keep it fast).")
    source = next((k for k, v in SOURCE_PROMPTS.items() if v == name), None)
    sample = [x for x in (signals or []) if x["source"] == source][:BATCH_SIZE]
    if source and sample:
        with st.expander("Preview: what the model receives for one batch of this source"):
            st.caption("System message")
            st.code(ai_service.extraction_system(source), language="text")
            st.caption("User message")
            st.code(f"Items from {source}:\n" + "\n".join(ai_service.format_item(x) for x in sample), language="text")
    st.caption("The prompts are plain files in the prompts/ folder; `git diff prompts` shows what you changed.")


# ------------------------------------------------------------------ state
# On first open, show the newest saved run so the demo works even without a new scan.
if "result" not in st.session_state:
    runs = storage.list_runs()
    st.session_state.result = storage.load_run(runs[0][0]) if runs else None

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("⚙️ Settings")
    host = st.text_input("Ollama address", DEFAULT_OLLAMA_HOST,
                         help="Your laptop: http://localhost:11434 · Office server: http://192.168.45.161:11434")
    installed = models_at(host)
    if installed:
        st.caption(f"🟢 Ollama reachable · {len(installed)} model(s) installed")
        options = installed
    else:
        st.caption("🔴 Ollama not reachable at this address")
        options = SUGGESTED_MODELS
    default_index = options.index(DEFAULT_MODEL) if DEFAULT_MODEL in options else 0
    model = st.selectbox("Model", options, index=default_index,
                         help="llama3.2:3b is fastest. qwen3:4b usually gives better reasons.")

    area = st.selectbox("Technology area", list(AREAS))
    sources = st.multiselect("Sources", SOURCES, default=SOURCES,
                             help=f"{REGISTRY_SOURCE} collect no signals: they look up the technologies the other sources "
                                  "found in npm, PyPI, Maven Central and Docker Hub, and rate their download numbers.")
    days = int(st.number_input("Look back (days)", min_value=1, value=DEFAULT_DAYS, step=1,
                               help="Any number of days, for example 365 or 3650 to include older projects. GitHub: repositories "
                                    "created in that window, most starred first. Hacker News: the most popular stories of the "
                                    "window. RSS feeds only list their newest items, so a long look-back adds little there."))
    if days > MAX_DAYS:
        st.caption(f"Longer than {MAX_DAYS:,} days is treated as {MAX_DAYS:,} days (100 years).")
    max_signals = st.slider("Signals sent to the LLM", 8, 80, DEFAULT_MAX_SIGNALS, step=8)
    top_n = st.slider("Technologies to rate", 5, 25, DEFAULT_TOP_N)
    use_cache = st.checkbox("Reuse sources fetched in the last hour", value=True,
                            help="Saves time and the GitHub rate limit while you tune prompts. Untick for fresh data.")
    calls = math.ceil(max_signals / BATCH_SIZE) + top_n * 7   # per technology: up to 5 source agents + anti-hype + judge
    st.caption(f"Up to ≈ {calls} LLM calls per scan (a source agent only runs when its source has data). "
               "On a CPU expect about 10–30 s each.")
    # Package registries only look up what the other sources found, so at least one of those is needed.
    scan = st.button("🔍 Scan & analyze", type="primary", disabled=not any(s != REGISTRY_SOURCE for s in sources))

    st.divider()
    st.subheader("🕘 Saved runs")
    runs = storage.list_runs()
    if runs:
        picked = st.selectbox("Run", runs, format_func=lambda r: r[1], label_visibility="collapsed")
        if st.button("Show this run"):
            st.session_state.result = storage.load_run(picked[0])
    else:
        st.caption("No saved runs yet.")

# ------------------------------------------------------------------ scan
if scan and not installed:
    st.sidebar.error(f"Can't reach Ollama at {host}. Start it (docker compose up -d) or fix the address.")
    scan = False
if scan and model not in installed:
    st.sidebar.error(f"Model {model} is not installed. Run: docker exec -it ollama ollama pull {model}")
    scan = False
if scan:
    settings = {"area": area, "sources": sources, "days": days, "max_signals": max_signals, "use_cache": use_cache,
                "top_n": top_n, "model": model, "host": host}
    with st.status("Scanning public sources and asking the local LLM...", expanded=True) as status:
        bar, line = st.progress(0.0), st.empty()

        def progress(message, fraction):
            bar.progress(min(max(fraction, 0.0), 1.0))
            line.write(message)

        result = pipeline.run_scan(settings, LLM(host, model), progress)
        storage.save_run(result)
        status.update(label=f"Scan finished in {result.get('duration_s', 0)} s", state="complete", expanded=False)
    st.session_state.result = result

# ------------------------------------------------------------------ header
st.title("📡 bbv Technology Radar")
result = st.session_state.result
if not result:
    st.write("Discover emerging technologies from GitHub, Y Combinator, Hacker News and tech blogs, "
             "and let a local LLM propose where they belong on bbv's radar.")
    st.info("Choose your settings on the left and click **Scan & analyze**.")
    st.markdown("""
**How it works**
1. **Collect** recent signals from the selected sources.
2. **Clean**: drop noise (funding, hiring…) and duplicates, mix sources fairly.
3. **Extract**: the LLM names the technologies the signals are about.
4. **Merge and rank**: the same technology from several sources counts more.
5. **Rate**: one agent per source scores the evidence in parallel (the package registries agent reads real npm, PyPI,
   Maven Central and Docker Hub download numbers), an anti-hype agent looks for reasons to reject, and a judge
   proposes a ring using a decision matrix; simple rules check it (e.g. a 3-month-old repo can't be *Adopt*).
""")
    with st.expander("🧠 Prompts"):
        prompt_editor([])
    st.stop()

s = result["settings"]
previous = storage.previous_run(result)
statuses = storage.compare(previous, result)
techs = result["technologies"]
proposed = techs

st.caption(f"Scan of {result['run_at'].replace('T', ' ')} · area **{s['area']}** · model **{s['model']}** · "
           f"compared with {'run of ' + previous['run_at'].replace('T', ' ') if previous else 'nothing (first run for this area)'}")

if result.get("errors"):
    with st.expander(f"⚠️ {len(result['errors'])} warning(s) during the scan"):
        for e in result["errors"]:
            st.write("- " + e)

# KPI row
k = st.columns(7)
k[0].metric("Signals analysed", result["stats"].get("signals", 0), help=f"{result['stats'].get('raw', 0)} collected before cleaning")
k[1].metric("Technologies rated", len(proposed))
for i, ring in enumerate(RINGS):
    k[2 + i].metric(f"{RING_ICON[ring]} {ring}", sum(1 for t in techs if t["ring"] == ring))
k[6].metric("✨ New", sum(1 for t in proposed if statuses.get(t["name"]) == "New"))

# Filters (one row, apply to every tab)
f1, f2, f3 = st.columns([2, 2, 2])
quadrant_filter = f1.multiselect("Quadrant", QUADRANTS, default=QUADRANTS)
relevance_filter = f2.multiselect("Relevance for bbv", ["HIGH", "MEDIUM", "LOW"], default=["HIGH", "MEDIUM", "LOW"])
search = f3.text_input("Search", placeholder="e.g. rust, agent, kubernetes")

shown = [t for t in techs
         if t["quadrant"] in quadrant_filter
         and t.get("relevance", "LOW") in relevance_filter
         and (not search or search.lower() in (t["name"] + " " + t.get("summary", "") + " " + t.get("reason", "")).lower())]

svg, numbered = radar_html(shown, statuses)

tab_radar, tab_insights, tab_table, tab_details, tab_signals, tab_prompts, tab_export = st.tabs(
    ["📡 Radar", "📊 Insights", "📋 Table", "🔎 Details", "🗂 Signals", "🧠 Prompts", "⬇️ Export"])

# ------------------------------------------------------------------ radar
with tab_radar:
    left, right = st.columns([3, 2])
    with left:
        radar_doc = f'<div style="max-width:720px;margin:0 auto">{svg}</div>'
        try:
            components.html(radar_doc, height=730)
        except AttributeError:  # fallback if a future Streamlit drops components.html
            st.html(radar_doc)
        st.caption("Hover a blip for its reason. Dashed ring = new since the last run of this area.")
    with right:
        for q in QUADRANTS:
            items = [t for t in numbered if t["quadrant"] == q]
            st.markdown(f"{swatch(q)}**{q}**", unsafe_allow_html=True)
            if not items:
                st.caption("Nothing here yet.")
            for t in items:
                status = STATUS_ICON.get(statuses.get(t["name"], ""), "")
                st.markdown(f"`{t['number']:>2}` **{t['name']}** · {RING_ICON[t['ring']]} {t['ring']}"
                            f"{' · ' + status if status else ''}")

# ------------------------------------------------------------------ insights
with tab_insights:
    if not numbered:
        st.info("No technologies match the filters.")
    else:
        df = pd.DataFrame([{"Technology": t["name"], "Ring": t["ring"], "Quadrant": t["quadrant"],
                            "Mentions": t["mentions"], "Sources": len(t["sources"]), "Relevance": t["relevance"]}
                           for t in numbered])
        qscale = alt.Scale(domain=QUADRANTS, range=QCOLORS)
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Where the radar sits")
            st.caption("Technologies per ring, split by quadrant")
            chart = alt.Chart(df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, stroke="#fcfcfb", strokeWidth=2).encode(
                x=alt.X("Ring:N", sort=RINGS, title=None),
                y=alt.Y("count():Q", title="Technologies"),
                color=alt.Color("Quadrant:N", scale=qscale, legend=alt.Legend(orient="bottom", title=None)),
                order=alt.Order("Quadrant:N"),
                tooltip=["Ring", "Quadrant", alt.Tooltip("count():Q", title="Technologies")],
            ).properties(height=320)
            st.altair_chart(chart, width="stretch")
        with c2:
            st.subheader("Strongest signals")
            st.caption("Mentions across all sources")
            top = df[df["Mentions"] > 0].sort_values("Mentions", ascending=False).head(12)
            chart = alt.Chart(top).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
                y=alt.Y("Technology:N", sort="-x", title=None),
                x=alt.X("Mentions:Q", title="Mentions", axis=alt.Axis(tickMinStep=1)),
                color=alt.Color("Quadrant:N", scale=qscale, legend=None),
                tooltip=["Technology", "Ring", "Quadrant", "Mentions", "Sources"],
            ).properties(height=320)
            st.altair_chart(chart, width="stretch")

        c3, c4 = st.columns(2)
        with c3:
            st.subheader("Where the signals came from")
            sig = pd.DataFrame(result["signals"])
            if not sig.empty:
                counts = sig.groupby("source").size().reset_index(name="Signals")
                chart = alt.Chart(counts).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color=QCOLORS[0]).encode(
                    y=alt.Y("source:N", sort="-x", title=None), x=alt.X("Signals:Q"),
                    tooltip=["source", "Signals"]).properties(height=200)
                st.altair_chart(chart, width="stretch")
        with c4:
            st.subheader("Relevance for bbv")
            rel = df[df["Mentions"] > 0]
            if not rel.empty:
                chart = alt.Chart(rel).mark_rect(stroke="#fcfcfb", strokeWidth=2).encode(
                    x=alt.X("Ring:N", sort=RINGS, title=None),
                    y=alt.Y("Relevance:N", sort=["HIGH", "MEDIUM", "LOW"], title=None),
                    color=alt.Color("count():Q", scale=alt.Scale(scheme="blues"), legend=None),
                    tooltip=["Ring", "Relevance", alt.Tooltip("count():Q", title="Technologies")],
                ).properties(height=200)
                text = alt.Chart(rel).mark_text(fontWeight="bold").encode(
                    x=alt.X("Ring:N", sort=RINGS), y=alt.Y("Relevance:N", sort=["HIGH", "MEDIUM", "LOW"]),
                    text="count():Q")
                st.altair_chart(chart + text, width="stretch")
                st.caption("Top-left (HIGH relevance, Adopt/Trial) is where bbv should act first.")

# ------------------------------------------------------------------ table
with tab_table:
    rows = [{
        "#": t["number"], "Technology": t["name"], "Ring": t["ring"], "Quadrant": t["quadrant"],
        "Status": statuses.get(t["name"], ""), "Relevance": t["relevance"], "Confidence": t["confidence"],
        "Mentions": t["mentions"], "Sources": ", ".join(t["sources"]), "Reason": t["reason"],
        "Evidence": t["evidence"][0]["url"] if t["evidence"] else None,
    } for t in numbered]
    st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={
        "Evidence": st.column_config.LinkColumn("Evidence", display_text="open"),
        "Mentions": st.column_config.NumberColumn(format="%d"),
    })

# ------------------------------------------------------------------ details
with tab_details:
    for ring in RINGS:
        items = [t for t in numbered if t["ring"] == ring]
        st.subheader(f"{RING_ICON[ring]} {ring} ({len(items)})")
        if not items:
            st.caption("No technologies in this ring.")
        for t in items:
            status = STATUS_ICON.get(statuses.get(t["name"], ""), "")
            with st.expander(f"{t['number']}. {t['name']} · {t['quadrant']} · {t['relevance']} relevance"
                             f"{' · ' + status if status else ''}"):
                st.write(f"**What it is:** {t['summary']}")
                st.write(f"**Why {ring}:** {t['reason']}")
                if t.get("business_value"):
                    st.write(f"**For bbv:** {t['business_value']}")
                st.write(f"**Confidence:** {t['confidence']} · **Mentions:** {t['mentions']} from "
                         f"{len(t['sources'])} source(s)")
                for note in t.get("rule_notes", []):
                    st.caption(f"Rule applied: {note}")
                if t.get("scorecards"):
                    st.write("**Agent scorecards** (0–10; for friction, hype and licensing risk, higher is worse)")
                    st.dataframe(pd.DataFrame([{
                        "Agent": c["lane"], "Scores": " · ".join(f"{k} {v}" for k, v in c["scores"].items() if v is not None) or "failed",
                        "Confidence": c["confidence"], "Summary": c["summary"]} for c in t["scorecards"]]), hide_index=True)
                if t.get("risk_memo"):
                    st.write(f"**Anti-hype review{' ⚠️ fatal flaw found' if t.get('fatal_flaws_found') else ''}:** {t['risk_memo']}")
                if t["evidence"]:
                    st.write("**Evidence**")
                    for e in t["evidence"]:
                        st.markdown(f"- [{e['title']}]({e['url']}) · {e['source']}{' · ' + e['date'] if e.get('date') else ''}")

# ------------------------------------------------------------------ signals
with tab_signals:
    sig = pd.DataFrame(result["signals"])
    if sig.empty:
        st.info("No signals in this run.")
    else:
        st.caption(f"{len(sig)} signals sent to the LLM, after removing noise and duplicates.")
        st.dataframe(sig[["n", "source", "title", "date", "url"]].rename(columns={"n": "#"}), hide_index=True,
                     column_config={"url": st.column_config.LinkColumn("Link", display_text="open")})

# ------------------------------------------------------------------ prompts
with tab_prompts:
    prompt_editor(result["signals"])

# ------------------------------------------------------------------ export
with tab_export:
    stamp = result["run_at"].replace(":", "-")
    st.write("Download this radar to share it or load it into other tools.")
    e1, e2, e3 = st.columns(3)
    e1.download_button("📄 Report (Markdown)", export.markdown_report(result, statuses, numbered),
                       file_name=f"tech-radar-{stamp}.md", mime="text/markdown")
    e2.download_button("📡 Thoughtworks radar (CSV)", export.byor_csv(numbered, statuses),
                       file_name=f"radar-{stamp}.csv", mime="text/csv")
    e3.download_button("🧾 Full data (JSON)", json.dumps(result, ensure_ascii=False, indent=2),
                       file_name=f"tech-radar-{stamp}.json", mime="application/json")
    st.caption("The CSV works with Thoughtworks *Build Your Own Radar* (it needs at least one technology in each of the four quadrants).")
