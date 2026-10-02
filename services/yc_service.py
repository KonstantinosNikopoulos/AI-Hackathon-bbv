"""Y Combinator: recent startups as a signal of where products and technologies are heading.
Based on the original service; now sorted by newest batch instead of file order."""
import re
from datetime import datetime

import requests

from config import AREAS

YC_API_URL = "https://yc-oss.github.io/api/companies/all.json"
SEASONS = {"winter": 1, "spring": 2, "summer": 3, "fall": 4, "autumn": 4}


def contains_keyword(text, keyword):
    return re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE) is not None


def batch_rank(company):
    """'Summer 2025' -> 20253. Unknown batches sort last."""
    batch = str(company.get("batch") or "")
    m = re.search(r"(winter|spring|summer|fall|autumn)\s*(\d{4})", batch, re.IGNORECASE)
    if m:
        return int(m.group(2)) * 10 + SEASONS[m.group(1).lower()]
    m = re.match(r"([WSXF])(\d{2})$", batch.strip(), re.IGNORECASE)  # short form like W25
    if m:
        season = {"W": 1, "X": 2, "S": 3, "F": 4}[m.group(1).upper()]
        return (2000 + int(m.group(2))) * 10 + season
    launched = company.get("launched_at")
    if isinstance(launched, (int, float)) and launched > 0:
        d = datetime.fromtimestamp(launched)
        return d.year * 10 + (d.month - 1) // 3 + 1
    return 0


def get_yc_companies(limit=5, technology_area="AI / LLM", companies=None):
    if companies is None:
        response = requests.get(YC_API_URL, timeout=30)
        response.raise_for_status()
        companies = response.json()

    keywords = AREAS.get(technology_area, AREAS["All"])["keywords"]
    matching = []
    for company in companies:
        if company.get("status") != "Active":
            continue
        name = company.get("name", "")
        description = company.get("one_liner") or company.get("long_description") or ""
        tags = company.get("tags", []) or []
        searchable = f"{name} {description} {' '.join(tags)}".lower()
        if keywords and not any(contains_keyword(searchable, k) for k in keywords):
            continue
        matching.append(company)

    matching.sort(key=batch_rank, reverse=True)

    results = []
    for company in matching[:limit]:
        description = company.get("one_liner") or company.get("long_description") or ""
        results.append({
            "source": "Y Combinator",
            "group": "ycombinator",
            "title": f"{company.get('name', '')} (YC {company.get('batch', '')})",
            "url": "https://www.ycombinator.com/companies/" + company.get("slug", ""),
            "text": f"{description} | tags: {', '.join((company.get('tags') or [])[:6])}",
            "date": "",
            "meta": {"batch": company.get("batch"), "location": company.get("all_locations", "")},
        })
    return results
