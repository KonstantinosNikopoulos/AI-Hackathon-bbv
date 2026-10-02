"""GitHub: new repositories that gained many stars quickly (based on the original service)."""
import os
from datetime import datetime, timedelta

import requests

from config import AREAS, clamp_days

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


def get_trending_repositories(limit=5, technology_area="AI / LLM", days=90, min_stars=100):
    token = os.getenv("GITHUB_TOKEN", "").strip().strip('"')
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "bbv-tech-radar"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    since_date = (datetime.now() - timedelta(days=clamp_days(days))).strftime("%Y-%m-%d")
    terms = AREAS.get(technology_area, AREAS["All"])["github"]
    query = f"{terms} created:>{since_date} stars:>{min_stars}".strip()

    response = requests.get(
        GITHUB_SEARCH_URL,
        params={"q": query, "sort": "stars", "order": "desc", "per_page": min(limit, 100)},   # 100 is the most GitHub gives per page
        headers=headers,
        timeout=15,
    )
    if response.status_code != 200:
        raise Exception(f"GitHub API error {response.status_code}: {response.text[:200]}")

    return [repo_signal(repo) for repo in response.json().get("items", [])]


def repo_signal(repo):
    """One GitHub repository (an item of the search answer or of /repos/<owner>/<name>) as a signal."""
    return {
        "source": "GitHub",
        "group": "github",
        "title": repo["full_name"] + (f" ({repo['language']})" if repo.get("language") else ""),
        "url": repo["html_url"],
        "text": f"{repo.get('description') or ''} | {repo['stargazers_count']} stars | "
                f"topics: {', '.join(repo.get('topics', [])[:6])}",
        "date": (repo.get("created_at") or "")[:10],
        "meta": {
            "name": repo["name"],
            "full_name": repo["full_name"],
            "description": repo.get("description") or "",
            "stars": repo["stargazers_count"],
            "language": repo.get("language"),
            "topics": repo.get("topics", []),
            "created_at": (repo.get("created_at") or "")[:10],
            # Already in the search response; shown to the rating prompt as facts (radar_record). "none" = no license at all.
            "pushed_at": (repo.get("pushed_at") or "")[:10],
            "forks": repo.get("forks_count", 0),
            "open_issues": repo.get("open_issues_count", 0),
            "license": (repo.get("license") or {}).get("spdx_id") or "none",
            "archived": bool(repo.get("archived")),
        },
    }
