"""Hacker News: popular stories of the last N days (Algolia API, no key needed)."""
import re
import time
from urllib.parse import urlparse

import requests

from config import AREAS

HN_URL = "https://hn.algolia.com/api/v1/search_by_date"


def get_hn_stories(limit=15, technology_area="All", days=30, min_points=150):
    since = int(time.time() - days * 86400)
    response = requests.get(
        HN_URL,
        params={"tags": "story", "numericFilters": f"points>{min_points},created_at_i>{since}", "hitsPerPage": 100},
        timeout=15,
    )
    response.raise_for_status()
    keywords = AREAS.get(technology_area, AREAS["All"])["keywords"]

    stories = []
    for hit in response.json().get("hits", []):
        title = hit.get("title") or ""
        if not title or title.lower().startswith(("ask hn: who is hiring", "ask hn: who wants to be hired")):
            continue
        if keywords and not any(re.search(r"\b" + re.escape(k) + r"\b", title, re.IGNORECASE) for k in keywords):
            continue
        stories.append({
            "source": "Hacker News",
            "group": "hackernews",
            "title": title,
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            "text": f"{hit.get('points', 0)} points, {hit.get('num_comments', 0)} comments on Hacker News",
            "date": (hit.get("created_at") or "")[:10],
            "meta": {"points": hit.get("points", 0), "comments": hit.get("num_comments", 0),
                     "domain": urlparse(hit.get("url") or "").netloc.replace("www.", "") or "news.ycombinator.com"},
        })
    stories.sort(key=lambda s: s["meta"]["points"], reverse=True)
    return stories[:limit]
