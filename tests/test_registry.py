"""Offline test of the package registry lookup (services/registry_service.py): no internet.
The fake answers have the shape of the real npm / pypistats / ecosyste.ms / Docker Hub answers.
Run from the project folder:  python tests/test_registry.py"""
import os
import re
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import registry_service as rs  # noqa: E402
from services.registry_service import lookup_registries, monthly, repo_slug  # noqa: E402

TODAY = date(2026, 10, 2)
rs.RETRY_WAIT = 0       # no real waiting in the test
rs.PYPISTATS_GAP = 0

NPM_MONTHS = [30_000_000, 33_000_000, 36_000_000, 40_500_000, 45_000_000, 51_000_000,
              57_000_000, 63_000_000, 72_000_000, 81_000_000, 90_000_000, 102_000_000]   # oldest first, divisible by 30
PYPI_MONTHS = [3_000_000, 3_300_000, 3_900_000, 4_500_000, 5_400_000, 6_300_000]


# ------------------------------------------------------------------ a fake network
CALLS, ROUTES = [], []


class Resp:
    def __init__(self, data=None, status=200):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def fake_get(url, params=None, headers=None, timeout=None):
    CALLS.append(url)
    assert headers and headers.get("User-Agent"), "registries ask for a User-Agent"
    for needle, answer in ROUTES:
        if needle in url:
            return answer(url, params) if callable(answer) else answer
    return Resp(status=404)


rs.requests.get = fake_get


def world(*routes):
    CALLS.clear()
    ROUTES[:] = routes


def npm_range(per_month):
    """The npm answer for the window in the URL: one entry per day, `per_month` (oldest first) spread evenly."""
    def answer(url, params):
        start, end = map(date.fromisoformat, re.search(r"range/([\d-]+):([\d-]+)/", url).groups())
        n, newest_first = (end - start).days + 1, per_month[::-1]
        return Resp({"start": str(start), "end": str(end), "package": "x", "downloads": [
            {"downloads": newest_first[min((n - 1 - i) // 30, len(newest_first) - 1)] // 30, "day": str(start + timedelta(days=i))}
            for i in range(n)]})
    return answer


def pypistats(per_month):
    """The pypistats answer: 187 days up to yesterday (older than 180 days must be ignored), without mirrors."""
    def answer(url, params):
        assert params == {"mirrors": "false"}, params
        end, newest_first = TODAY - timedelta(days=1), per_month[::-1]
        rows = [{"category": "without_mirrors", "date": str(end - timedelta(days=age)),
                 "downloads": newest_first[age // 30] // 30 if age < 180 else 999_999_999} for age in range(186, -1, -1)]
        return Resp({"data": rows, "package": "x", "type": "overall_downloads"})
    return answer


def eco(**changes):
    """An ecosyste.ms package answer (the fields the lookup reads)."""
    return Resp({"name": "x", "latest_release_number": "1.9.1", "latest_release_published_at": "2026-03-25T15:53:54.092Z",
                 "first_release_published_at": "2020-02-05T23:02:45.340Z", "versions_count": 64,
                 "dependent_packages_count": 1073, "dependent_repos_count": 66556, "downloads": 345_663_925,
                 "downloads_period": "last-month", "repository_url": "https://github.com/open-telemetry/opentelemetry-js",
                 **changes})


def docker(pulls, stars=100, registered="2014-06-05T20:04:50Z", updated="2026-09-29T15:06:05.096148Z"):
    return Resp({"pull_count": pulls, "star_count": stars, "date_registered": registered, "last_updated": updated})


def called(host):
    return [u for u in CALLS if host in u]


# ------------------------------------------------------------------ helpers
assert monthly([1] * 60, 12) == [30, 30]
assert monthly([0] * 30 + [1] * 30, 12) == [30], "months before the first download are dropped"
assert monthly([1] * 45, 12) == [30], "a partial month is ignored"
assert monthly([5] * 10, 12) == [] and monthly([], 12) == []
assert monthly([1] * 360, 6) == [30] * 6, "never more months than asked for"
assert repo_slug("git+https://github.com/expressjs/express.git") == "expressjs/express"
assert repo_slug("https://github.com/Astral-sh/ty/") == "astral-sh/ty" and repo_slug("git@github.com:psf/requests.git") == "psf/requests"
assert repo_slug("https://github.com/x/y.js#readme") == "x/y.js"
assert repo_slug("https://gitlab.com/x/y") is None and repo_slug(None) is None and repo_slug("") is None
assert repo_slug("https://github.com/../x") is None and repo_slug("https://github.com/org/.github") == "org/.github"

# the curated map that ships in config.py: a typo here would silently mean "no data", so check its shape
import config  # noqa: E402

for tech, entry in config.PACKAGE_MAP.items():
    assert tech == tech.lower().strip(), f"{tech}: keys are the lower case technology name"
    assert entry and set(entry) <= set(rs.LABELS), f"{tech}: unknown registry in {sorted(entry)}"
    assert all(isinstance(names, list) and names for names in entry.values()), tech
    assert all(":" in n for n in entry.get("maven", [])), f"{tech}: Maven names are group:artifact"
    assert all(n.count("/") == 1 for n in entry.get("docker", [])), f"{tech}: Docker names are namespace/image"
print("OK helpers")

# ------------------------------------------------------------------ curated npm package
world(("packages.ecosyste.ms/api/v1/registries/npmjs.org/packages/%40opentelemetry%2Fapi", eco()),
      ("api.npmjs.org/downloads/range/", npm_range(NPM_MONTHS)))
errors = []
items = lookup_registries("OpenTelemetry", "opentelemetry", curated={"npm": ["@opentelemetry/api"]}, errors=errors, today=TODAY)
assert errors == [] and len(items) == 1
item, m = items[0], items[0]["meta"]
assert item["source"] == "Package registries" and item["title"] == "npm: @opentelemetry/api"
assert item["url"] == "https://www.npmjs.com/package/@opentelemetry/api" and item["date"] == "2026-03-25"
assert "102,000,000 downloads in the last 30 days" in item["text"] and "1,073 dependent packages" in item["text"]
assert m["registry"] == "npm" and m["match"] == "curated" and m["official"] is False
assert m["monthly"] == NPM_MONTHS and m["last_30d"] == NPM_MONTHS[-1], "12 months, newest last"
assert m["window_days"] == 360 and m["window_total"] == sum(NPM_MONTHS)
assert (m["dependent_packages"], m["dependent_repos"], m["versions"], m["latest"]) == (1073, 66556, 64, "1.9.1")
assert (m["first_release"], m["latest_release"], m["repository"]) == ("2020-02-05", "2026-03-25", "open-telemetry/opentelemetry-js")
end = TODAY - timedelta(days=2)   # npm counts a day about two days late: the newest two days would read 0
assert called("api.npmjs.org") == [f"https://api.npmjs.org/downloads/range/{end - timedelta(days=359)}:{end}/@opentelemetry/api"]
assert not called("hub.docker.com"), "a curated entry lists exactly what to look up"
print("OK curated npm package: 12 monthly totals, window ends two days ago, dependents and release dates")

# ------------------------------------------------------------------ curated PyPI package: name form, pypistats, rate limit
world(("packages.ecosyste.ms/api/v1/registries/pypi.org/packages/opentelemetry-api", eco(downloads=387_908_697)),
      ("pypistats.org/api/packages/opentelemetry-api/overall", pypistats(PYPI_MONTHS)))
items = lookup_registries("OpenTelemetry", "opentelemetry", curated={"pypi": ["OpenTelemetry_API"]}, errors=[], today=TODAY)
m = items[0]["meta"]
assert items[0]["title"] == "PyPI: opentelemetry-api" and items[0]["url"] == "https://pypi.org/project/opentelemetry-api/"
assert m["monthly"] == PYPI_MONTHS and m["last_30d"] == PYPI_MONTHS[-1] and m["window_days"] == 180, "the 7 oldest days are ignored"
assert m["first_release"] is None, "PyPI names get reused: the first release says nothing about this technology"

hits = []


def throttled(url, params):
    hits.append(url)
    return Resp(status=429) if len(hits) == 1 else pypistats(PYPI_MONTHS)(url, params)


world(("packages.ecosyste.ms/api/v1/registries/pypi.org/", eco()), ("pypistats.org", throttled))
errors = []
items = lookup_registries("X", "x", curated={"pypi": ["mcp"]}, errors=errors, today=TODAY)
assert len(hits) == 2 and errors == [] and items[0]["meta"]["monthly"] == PYPI_MONTHS, "HTTP 429 is retried once"

world(("packages.ecosyste.ms/api/v1/registries/pypi.org/", eco(downloads=219_043_071)), ("pypistats.org", Resp(status=429)))
errors = []
items = lookup_registries("X", "x", curated={"pypi": ["mcp"]}, errors=errors, today=TODAY)
m = items[0]["meta"]
assert errors == ["PyPI: HTTP 429 from pypistats.org"], errors
assert m["monthly"] == [] and m["last_30d"] == 219_043_071, "still rate limited: the last month from ecosyste.ms, no trend"
print("OK curated PyPI package: normalized name, 180-day window, one retry on HTTP 429, fallback without a trend")

# ------------------------------------------------------------------ a guessed name is only evidence when its repository proves it
world(("registries/npmjs.org/packages/ty", eco(repository_url="https://github.com/someone-else/ty")),   # a different "ty"
      ("registries/pypi.org/packages/ty", eco(repository_url="git+https://github.com/Astral-sh/ty.git", downloads=39_031_712)),
      ("pypistats.org/api/packages/ty/overall", pypistats(PYPI_MONTHS)),
      ("api.npmjs.org", npm_range(NPM_MONTHS)))
errors = []
items = lookup_registries("ty", "ty", own_repos=["https://github.com/astral-sh/ty"], errors=errors, today=TODAY)
assert errors == [] and [i["title"] for i in items] == ["PyPI: ty"], [i["title"] for i in items]
assert items[0]["meta"]["match"] == "verified" and items[0]["meta"]["repository"] == "astral-sh/ty"
assert not called("api.npmjs.org"), "the npm package of that name belongs to another repository: no numbers are fetched for it"

world(("api.npmjs.org", npm_range(NPM_MONTHS)), ("registries/", eco()), ("pypistats.org", pypistats(PYPI_MONTHS)))
assert lookup_registries("go", "go", errors=[], today=TODAY) == [], "a name alone is never evidence"
assert not called("api.npmjs.org") and not called("pypistats") and not called("ecosyste.ms"), CALLS
assert called("hub.docker.com") == ["https://hub.docker.com/v2/repositories/library/go/"], "only an official image can be taken by name"
print("OK guessed names: the repository link decides, no own repository means no npm or PyPI lookup")

# ------------------------------------------------------------------ Docker Hub: official image, project-owned image
world(("repositories/library/redis/", docker(11_371_016_757, 13620)))
items = lookup_registries("Redis", "redis", errors=[], today=TODAY)
m = items[0]["meta"]
assert len(items) == 1 and items[0]["title"] == "Docker Hub: library/redis" and items[0]["url"] == "https://hub.docker.com/_/redis"
assert (m["official"], m["match"], m["lifetime"], m["stars"]) == (True, "official", 11_371_016_757, 13620)
assert (m["first_release"], m["latest_release"]) == ("2014-06-05", "2026-09-29")

world(("repositories/grafana/grafana/", docker(5_342_709_460, 3586)))   # no official image; the project publishes its own
items = lookup_registries("Grafana", "grafana", own_repos=["https://github.com/grafana/grafana"], errors=[], today=TODAY)
m = items[0]["meta"]
assert len(items) == 1 and items[0]["url"] == "https://hub.docker.com/r/grafana/grafana"
assert (m["official"], m["match"], m["lifetime"]) == (False, "owner", 5_342_709_460), "unofficial = published by the project"
assert called("registries/npmjs.org") and called("registries/pypi.org"), "an own repository allows the npm and PyPI check"

world(("repositories/library/redis/", docker(11_371_016_757)), ("repositories/redis/redis/", docker(1_000_000)))
items = lookup_registries("Redis", "redis", own_repos=["https://github.com/redis/redis"], errors=[], today=TODAY)
assert [(i["meta"]["official"], i["meta"]["match"]) for i in items] == [(True, "official"), (False, "owner")], "official vs unofficial"
print("OK Docker Hub: official image, and an unofficial image of the project's own")

# ------------------------------------------------------------------ Maven Central: curated, no download numbers
world(("registries/repo1.maven.org/packages/io.opentelemetry%3Aopentelemetry-api",
       eco(downloads=None, downloads_period=None, latest_release_number="1.66.0", latest_release_published_at="2026-09-12T02:20:56.000Z",
           dependent_packages_count=624, dependent_repos_count=1029, versions_count=104)))
items = lookup_registries("OpenTelemetry", "opentelemetry", curated={"maven": ["io.opentelemetry:opentelemetry-api"]}, errors=[],
                          today=TODAY)
m = items[0]["meta"]
assert items[0]["title"] == "Maven Central: io.opentelemetry:opentelemetry-api"
assert items[0]["url"] == "https://central.sonatype.com/artifact/io.opentelemetry/opentelemetry-api"
assert m["last_30d"] is None and m["monthly"] == [] and m["window_days"] is None, "Maven Central publishes no download counts"
assert (m["dependent_packages"], m["dependent_repos"], m["versions"], m["latest"]) == (624, 1029, 104, "1.66.0")
assert not called("npmjs") and not called("pypistats"), CALLS
print("OK curated Maven package: dependents and releases, no downloads")

# ------------------------------------------------------------------ nothing found, odd names, failures
world()
assert lookup_registries("Nothing", "nothing", errors=(errs := []), today=TODAY) == [] and errs == [], "not found is not an error"
assert lookup_registries("C#", "c#", errors=[], today=TODAY) == [] and CALLS == ["https://hub.docker.com/v2/repositories/library/nothing/"], CALLS

ConnectionFailure = rs.requests.ConnectionError if hasattr(rs.requests, "ConnectionError") else OSError


def refuse(url, params):
    raise ConnectionFailure("no route to host")


world(("packages.ecosyste.ms", refuse), ("api.npmjs.org", refuse), ("repositories/library/", Resp(status=500)))
errors = []
assert lookup_registries("X", "x", curated={"npm": ["x"], "docker": ["library/x"]}, errors=errors, today=TODAY) == []
assert errors == ["ecosyste.ms: no route to host", "npm: no route to host", "Docker Hub: HTTP 500 from hub.docker.com"], errors

world(("packages.ecosyste.ms", refuse), ("api.npmjs.org/downloads/range/", npm_range(NPM_MONTHS)))   # metadata down, downloads up
errors = []
items = lookup_registries("X", "x", curated={"npm": ["x"]}, errors=errors, today=TODAY)
assert errors == ["ecosyste.ms: no route to host"] and items[0]["meta"]["monthly"] == NPM_MONTHS and items[0]["meta"]["repository"] is None
world(("packages.ecosyste.ms", refuse), ("api.npmjs.org/downloads/range/", npm_range(NPM_MONTHS)))
assert lookup_registries("X", "x", own_repos=["https://github.com/a/x"], errors=[], today=TODAY) == [], "cannot verify a guess: no record"

world(("registries/npmjs.org/", eco()), ("api.npmjs.org", npm_range(NPM_MONTHS)))
many = lookup_registries("X", "x", curated={"npm": [f"pkg{i}" for i in range(9)]}, errors=[], today=TODAY)
assert len(many) == rs.MAX_RECORDS
print("OK nothing found, odd names, a registry that is down is a warning and the others still answer")
print("ALL REGISTRY TESTS PASSED")
