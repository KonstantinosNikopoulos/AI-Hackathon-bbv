"""Downloads: Thoughtworks Build Your Own Radar CSV and a Markdown report."""
import csv
import html
import io

from config import REGISTRY_SOURCE


def byor_csv(technologies, statuses):
    """Columns: name, ring, quadrant, isNew, status, description (HTML allowed)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["name", "ring", "quadrant", "isNew", "status", "description"])
    for t in technologies:
        status = statuses.get(t["name"], "")
        links = "<br>".join(f'<a href="{html.escape(e["url"])}">{html.escape(e["title"])}</a>' for e in t["evidence"][:3])
        desc = (f"<p>{html.escape(t.get('reason', ''))}</p>"
                f"<p><strong>For bbv:</strong> {html.escape(t.get('business_value', ''))}</p>{links}")
        writer.writerow([t["name"], t["ring"].lower(), t["quadrant"], "TRUE" if status == "New" else "FALSE",
                         status or "No Change", desc])
    return out.getvalue()


def markdown_report(result, statuses, numbered):
    s = result["settings"]
    signal_sources = [x for x in s["sources"] if x != REGISTRY_SOURCE]   # package registries add no signals
    registries = " · package registries checked" if REGISTRY_SOURCE in s["sources"] else ""
    lines = [
        "# bbv Technology Radar",
        "",
        f"Scan: {result['run_at'].replace('T', ' ')} · area {s['area']} · model {s['model']} · "
        f"{result['stats'].get('signals', 0)} signals from {', '.join(signal_sources)}{registries}",
        "",
        "Rings are proposals by a local LLM, checked against simple rules. Review before use.",
        "",
    ]
    for ring in ["Adopt", "Trial", "Assess", "Hold"]:
        items = [t for t in numbered if t["ring"] == ring]
        if not items:
            continue
        lines += [f"## {ring}", ""]
        for t in items:
            status = statuses.get(t["name"], "")
            lines.append(f"**{t['number']}. {t['name']}** ({t['quadrant']}{', ' + status if status else ''}) "
                         f"- relevance {t['relevance']}, confidence {t['confidence']}")
            if t.get("reason"):
                lines.append(f"  {t['reason']}")
            for e in t["evidence"][:3]:
                lines.append(f"  - [{e['title']}]({e['url']}) ({e['source']})")
            lines.append("")
    return "\n".join(lines)
