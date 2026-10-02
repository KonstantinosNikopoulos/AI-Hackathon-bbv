"""Package registries: how much is one technology really downloaded and depended on? (npm, PyPI, Maven Central, Docker Hub)

No API keys. Download numbers come from the registries themselves (npm downloads API, pypistats.org, Docker Hub). Reverse
dependencies, release dates and the repository link come from ecosyste.ms, because deps.dev counts dependents per package
VERSION, so a package that just released looks tiny (express 4.21.2: 432 direct dependents; ecosyste.ms counts 93,237
dependent packages for express as a whole).

A name is not proof (npm has a package called "go"), so a package is only returned when it can be tied to the technology:
  curated   listed for the technology in config.PACKAGE_MAP
  verified  its repository link is one of the technology's own GitHub repositories (pipeline.merge_candidates: own_repos)
  official  a Docker official image (library/<name>)
  owner     a Docker image of that name under the owner of the technology's GitHub repository
Everything comes back as evidence items (source "Package registries"): the rating prompt reads them as dated lines
(ai_service.evidence_lines) which is how the prompt sees download numbers. A package the registry does not know is not an error; a registry that fails adds a warning to `errors`."""
import re
import threading
import time
from datetime import date, timedelta
from urllib.parse import quote

import requests

from config import REGISTRY_SOURCE

HEADERS = {"User-Agent": "bbv-tech-radar"}
TIMEOUT = (5, 20)        # connect, read: seconds
MONTH_DAYS = 30          # a "month" is 30 days, so every month has the same length
NPM_MONTHS = 12          # the npm API answers at most 18 months in one call (and silently cuts longer requests)
NPM_LAG_DAYS = 2         # npm counts a day about two days late: the newest two days read 0
PYPI_MONTHS = 6          # pypistats keeps 180 days
PYPISTATS_GAP = 1.0      # seconds between pypistats requests: it answers bursts with HTTP 429
RETRY_WAIT = 5           # seconds to wait before the one retry after an HTTP 429
MAX_RECORDS = 6          # packages per technology that reach the prompt

LABELS = {"npm": "npm", "pypi": "PyPI", "maven": "Maven Central", "docker": "Docker Hub"}
ECOSYSTEMS = {"npm": "npmjs.org", "pypi": "pypi.org", "maven": "repo1.maven.org"}   # ecosyste.ms registry names
PAGES = {"npm": "https://www.npmjs.com/package/{}", "pypi": "https://pypi.org/project/{}/",
         "maven": "https://central.sonatype.com/artifact/{}", "docker": "https://hub.docker.com/r/{}"}
GITHUB = re.compile(r"github\.com[/:]+([a-z0-9][\w.-]*)/([\w.-]+?)(?:\.git)?(?:[/#?]|$)", re.IGNORECASE)
NAME_OK = re.compile(r"^[a-z0-9][a-z0-9._-]+$")   # what a package or image name can look like (so "c#" is not looked up)

_pypistats_lock = threading.Lock()
_pypistats_last = [0.0]   # when the last pypistats request ended (time.monotonic)


def repo_slug(url):
    """'git+https://github.com/Astral-sh/ty.git' -> 'astral-sh/ty'. None when it is not a GitHub URL."""
    m = GITHUB.search(url or "")
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def monthly(daily, months):
    """Downloads per day (oldest first) -> totals of consecutive 30-day windows that end on the last day, oldest first.
    Months before the first download are dropped, so the length of the result is the download history in months."""
    end, n = len(daily), min(months, len(daily) // MONTH_DAYS)
    totals = [sum(daily[end - (i + 1) * MONTH_DAYS:end - i * MONTH_DAYS]) for i in reversed(range(n))]
    while totals and totals[0] == 0:
        totals.pop(0)
    return totals


def _day(value):
    return (value or "")[:10] or None


def _get_json(url, params=None):
    """The parsed answer. None when the registry does not know the package (404). Anything else raises."""
    response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    if response.status_code == 429:   # rate limited: one more try after a pause
        time.sleep(RETRY_WAIT)
        response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} from {url.split('/')[2]}")
    return response.json()


def _npm_daily(name, today):
    """Downloads per day for the last NPM_MONTHS x 30 days, ending when npm's numbers are complete. None: unknown package."""
    end = today - timedelta(days=NPM_LAG_DAYS)
    start = end - timedelta(days=NPM_MONTHS * MONTH_DAYS - 1)
    data = _get_json(f"https://api.npmjs.org/downloads/range/{start}:{end}/{name}")
    if data is None:
        return None
    by_day = {d["day"]: d["downloads"] for d in data.get("downloads", [])}
    return [by_day.get((start + timedelta(days=i)).isoformat(), 0) for i in range(NPM_MONTHS * MONTH_DAYS)]


def _pypi_daily(name):
    """Downloads per day for the last PYPI_MONTHS x 30 days (without mirrors). None: unknown package or no numbers."""
    with _pypistats_lock:   # one pypistats request at a time, a second apart: it blocks bursts
        pause = PYPISTATS_GAP - (time.monotonic() - _pypistats_last[0])
        if pause > 0:
            time.sleep(pause)
        try:
            data = _get_json(f"https://pypistats.org/api/packages/{name}/overall", {"mirrors": "false"})
        finally:
            _pypistats_last[0] = time.monotonic()
    by_day = {r["date"]: r["downloads"] for r in (data or {}).get("data", []) if r.get("category") == "without_mirrors"}
    if not by_day:
        return None
    end, n = date.fromisoformat(max(by_day)), PYPI_MONTHS * MONTH_DAYS
    return [by_day.get((end - timedelta(days=n - 1 - i)).isoformat(), 0) for i in range(n)]


def _ecosystems(registry, name):
    """Package-level facts from ecosyste.ms. {} when it does not know the package."""
    data = _get_json(f"https://packages.ecosyste.ms/api/v1/registries/{ECOSYSTEMS[registry]}/packages/{quote(name, safe='')}")
    if not data:
        return {}
    return {"dependent_packages": data.get("dependent_packages_count"), "dependent_repos": data.get("dependent_repos_count"),
            "versions": data.get("versions_count"), "latest": data.get("latest_release_number"),
            "latest_release": _day(data.get("latest_release_published_at")),
            # PyPI names get reused (ecosyste.ms dates "ty" 2017, long before Astral's tool), so a first release means nothing there.
            "first_release": None if registry == "pypi" else _day(data.get("first_release_published_at")),
            "repository": repo_slug(data.get("repository_url")),
            "downloads": data.get("downloads") if data.get("downloads_period") == "last-month" else None}


def _package(registry, name, own, today, errors):
    """One npm, PyPI or Maven package as `meta`, or None. `own`: the technology's GitHub repositories ('owner/repo'); the
    package has to link to one of them. None means the package is curated and needs no proof."""
    if registry == "pypi":
        name = re.sub(r"[-_.]+", "-", name).lower()   # PyPI treats - _ . as the same letter; pypistats wants this form
    try:
        facts = _ecosystems(registry, name)
    except Exception as error:   # unreachable: fine for a curated package, no proof for a guessed one
        errors.append(f"ecosyste.ms: {error}")
        facts = {}
    if own is not None and facts.get("repository") not in own:
        return None
    daily = None
    if registry != "maven":   # Maven Central publishes no download numbers
        try:
            daily = _npm_daily(name, today) if registry == "npm" else _pypi_daily(name)
        except Exception as error:
            errors.append(f"{LABELS[registry]}: {error}")
    if not facts and daily is None:
        return None
    return {"registry": registry, "package": name, "match": "curated" if own is None else "verified", "official": False,
            "last_30d": sum(daily[-MONTH_DAYS:]) if daily else facts.get("downloads"),
            "monthly": monthly(daily or [], NPM_MONTHS if registry == "npm" else PYPI_MONTHS),
            "window_days": len(daily) if daily else None, "window_total": sum(daily) if daily else None,
            **{k: facts.get(k) for k in ("dependent_packages", "dependent_repos", "versions", "latest", "latest_release",
                                         "first_release", "repository")}}


def _docker(repo, match, errors):
    """One Docker Hub image ('namespace/name') as `meta` with its lifetime pulls, or None."""
    try:
        data = _get_json(f"https://hub.docker.com/v2/repositories/{repo}/")
    except Exception as error:
        errors.append(f"Docker Hub: {error}")
        return None
    if not data:
        return None
    return {"registry": "docker", "package": repo, "match": match, "official": repo.startswith("library/"),
            "lifetime": data.get("pull_count"), "stars": data.get("star_count"),
            "first_release": _day(data.get("date_registered")), "latest_release": _day(data.get("last_updated"))}


def _evidence(meta):
    """The item the pipeline stores and the prompt reads: the usual evidence fields, with the numbers in `meta`."""
    registry, package = meta["registry"], meta["package"]
    if registry == "docker" and meta["official"]:
        url = f"https://hub.docker.com/_/{package.split('/')[1]}"
    else:
        url = PAGES[registry].format(package.replace(":", "/") if registry == "maven" else package)
    facts = []
    if meta.get("last_30d") is not None:
        facts.append(f"{meta['last_30d']:,} downloads in the last 30 days")
    if meta.get("lifetime") is not None:
        facts.append(f"{meta['lifetime']:,} pulls in total")
    if meta.get("dependent_packages") is not None:
        facts.append(f"{meta['dependent_packages']:,} dependent packages")
    return {"source": REGISTRY_SOURCE, "title": f"{LABELS[registry]}: {package}", "url": url,
            "text": ", ".join(facts) or "no download or dependency numbers", "date": meta.get("latest_release") or "", "meta": meta}


def lookup_registries(name, key, own_repos=(), curated=None, errors=None, today=None):
    """Evidence items for ONE technology; [] when no package can be tied to it.
    name, key: as pipeline.merge_candidates names it. own_repos: urls of GitHub repositories that ARE the technology.
    curated: its entry in config.PACKAGE_MAP, e.g. {"npm": ["react"], "maven": ["group:artifact"], "docker": ["library/redis"]}.
    errors: a list that receives one warning per registry that failed."""
    errors = [] if errors is None else errors
    own = {s for s in map(repo_slug, own_repos or ()) if s}
    slug = re.sub(r"\s+", "-", key.strip())
    if curated:
        plan = [(registry, package, "curated") for registry in LABELS for package in curated.get(registry, [])]
    elif NAME_OK.match(slug):
        plan = [("docker", f"library/{slug}", "official")]
        plan += [("docker", f"{owner}/{slug}", "owner") for owner in sorted({s.split("/")[0] for s in own})]
        if own:   # an npm or PyPI package of that name is only evidence when its repository link proves it
            plan = [("npm", slug, "verified"), ("pypi", slug, "verified")] + plan
    else:
        plan = []
    today = today or date.today()
    metas = []
    for registry, package, match in plan:
        meta = (_docker(package, match, errors) if registry == "docker"
                else _package(registry, package, own if match == "verified" else None, today, errors))
        if meta:
            metas.append(meta)
    return [_evidence(m) for m in metas[:MAX_RECORDS]]
