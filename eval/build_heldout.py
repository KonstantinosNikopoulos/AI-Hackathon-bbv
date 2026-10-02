"""Draws the HELD-OUT test set, eval/heldout_radar.json: technologies that nobody looked at while the decision rules were designed.

gold_radar.json is the set the rules were diagnosed on. To know that a rule change is not just fitted to those 29 technologies, the
result is also measured on this second set, which is drawn here by rules and a fixed seed, not by hand:
  - up to 32 projects drawn at random from the public CNCF landscape, 8 per maturity level (fewer when too few qualify) (Graduated -> Adopt, Incubating -> Trial,
    Sandbox -> Assess, Archived -> Hold), each with a GitHub repository, never one that is already in gold_radar.json.
    Graduated needs 10,000 stars, Incubating 1,000, Sandbox 100 (so "Adopt" means widely used), Archived needs an archived repository.
  - 12 hand-listed technologies outside cloud native whose ring is a fact (6 Adopt, 6 Hold), each with a check.
Run it once:  python eval/build_heldout.py        (needs internet; GITHUB_TOKEN in .env avoids rate limits)
"""
import json
import os
import random
import sys

import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config  # noqa: E402,F401  (loads .env, so GITHUB_TOKEN is used)

SEED, PER_LEVEL = 2026, 8
MIN_STARS = {"graduated": 10000, "incubating": 1000, "sandbox": 100, "archived": 0}
RING = {"graduated": "Adopt", "incubating": "Trial", "sandbox": "Assess", "archived": "Hold"}
MEANING = {"graduated": "a CNCF graduated project: the highest maturity level", "incubating": "a CNCF incubating project: proven by several adopters, not yet graduated",
           "sandbox": "a CNCF sandbox project: early stage, worth a spike", "archived": "a CNCF archived project: no longer maintained"}
HEADERS = {"User-Agent": "bbv-tech-radar", "Accept": "application/json"}
if os.getenv("GITHUB_TOKEN", "").strip():
    HEADERS["Authorization"] = "Bearer " + os.getenv("GITHUB_TOKEN").strip().strip('"')

# Outside cloud native, ring by fact. Adopt: universal and maintained. Hold: support ended.
HAND = [
    ("Java", "Adopt", "Languages & Frameworks", "ubiquity", "The Java platform (OpenJDK).", "The OpenJDK repository is pushed to daily.", "https://github.com/openjdk/jdk",
     {"type": "github_active", "repo": "openjdk/jdk", "min_stars": 10000, "max_days_since_push": 30}, "openjdk/jdk", {}),
    ("Node.js", "Adopt", "Platforms", "ubiquity", "The JavaScript runtime.", "The official Node.js Docker image has over 7 billion pulls.", "https://hub.docker.com/_/node",
     {"type": "docker_pulls", "image": "library/node", "min": 1000000000}, "nodejs/node", {"docker": ["library/node"]}),
    ("Docker", "Adopt", "Tools", "ubiquity", "The container engine.", "The Moby repository behind Docker has tens of thousands of stars and is pushed to daily.", "https://github.com/moby/moby",
     {"type": "github_active", "repo": "moby/moby", "min_stars": 50000, "max_days_since_push": 30}, "moby/moby", {}),
    ("nginx", "Adopt", "Platforms", "ubiquity", "The web server and reverse proxy.", "The official nginx Docker image has over a billion pulls.", "https://hub.docker.com/_/nginx",
     {"type": "docker_pulls", "image": "library/nginx", "min": 1000000000}, "nginx/nginx", {"docker": ["library/nginx"]}),
    ("Go", "Adopt", "Languages & Frameworks", "ubiquity", "The Go programming language.", "The official golang Docker image has over 2 billion pulls and the repository is pushed to daily.", "https://hub.docker.com/_/golang",
     {"type": "docker_pulls", "image": "library/golang", "min": 1000000000}, "golang/go", {"docker": ["library/golang"]}),
    ("Spring Boot", "Adopt", "Languages & Frameworks", "ubiquity", "The Java application framework.", "The spring-boot repository has over 70,000 stars and is pushed to daily.", "https://github.com/spring-projects/spring-boot",
     {"type": "github_active", "repo": "spring-projects/spring-boot", "min_stars": 50000, "max_days_since_push": 30}, "spring-projects/spring-boot", {}),
    ("Dockershim", "Hold", "Platforms", "lifecycle", "The Kubernetes component that let it use Docker as a container runtime.", "Kubernetes removed dockershim in version 1.24 (May 2022).",
     "https://kubernetes.io/blog/2022/02/17/dockershim-faq/", {"type": "manual"}, None, {}),
    ("PodSecurityPolicy", "Hold", "Techniques", "lifecycle", "The Kubernetes admission control for pod security.", "Kubernetes removed PodSecurityPolicy in version 1.25 (August 2022).",
     "https://kubernetes.io/docs/concepts/security/pod-security-policy/", {"type": "manual"}, None, {}),
    ("Helm 2", "Hold", "Tools", "lifecycle", "The second major version of the Helm package manager for Kubernetes, with its Tiller server.", "Helm v2 support ended on 2020-11-13; Helm 3 replaced it.",
     "https://helm.sh/blog/helm-v2-deprecation-timeline/", {"type": "manual"}, None, {}),
    ("CentOS Linux", "Hold", "Platforms", "lifecycle", "The community rebuild of Red Hat Enterprise Linux (not CentOS Stream).", "CentOS Linux 7, the last release, reached end of life on 2024-06-30.",
     "https://endoflife.date/centos", {"type": "eol", "product": "centos", "cycle": "7"}, None, {}),
    ("PHP 5", "Hold", "Languages & Frameworks", "lifecycle", "The PHP 5 language line.", "PHP 5.6, the last release of the line, reached end of life on 2018-12-31.",
     "https://endoflife.date/php", {"type": "eol", "product": "php", "cycle": "5.6"}, None, {}),
    ("Apache Struts 1", "Hold", "Languages & Frameworks", "lifecycle", "The first version of the Apache Struts web framework.", "Struts 1 reached end of life in 2013 and its GitHub repository is archived.",
     "https://struts.apache.org/struts1eol-announcement.html", {"type": "github_archived", "repo": "apache/struts1"}, "apache/struts1", {}),
]


def github_repo(url):
    path = url.split("github.com/")[1].strip("/").removesuffix(".git") if "github.com/" in (url or "") else None
    if not path or path.count("/") != 1:
        return None, None
    r = requests.get(f"https://api.github.com/repos/{path}", headers=HEADERS, timeout=25)
    return (path, r.json()) if r.status_code == 200 else (path, None)


def main():
    gold = json.load(open(os.path.join(ROOT, "eval", "gold_radar.json"), encoding="utf-8"))["items"]
    taken_names, taken_repos = {i["name"].lower() for i in gold}, {i["repo"].lower() for i in gold if i["repo"]}
    land = yaml.safe_load(requests.get("https://raw.githubusercontent.com/cncf/landscape/master/landscape.yml", timeout=60).text)
    projects = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("project") and node.get("name") and node.get("repo_url"):
                projects.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(land)
    rng, items = random.Random(SEED), []
    for level in ("graduated", "incubating", "sandbox", "archived"):
        pool = sorted((p for p in projects if p["project"] == level), key=lambda p: p["name"])
        rng.shuffle(pool)
        got = 0
        for p in pool:
            if got == PER_LEVEL:
                break
            path, repo = github_repo(p["repo_url"])
            if repo is None or p["name"].lower() in taken_names or path.lower() in taken_repos:
                continue
            if repo["stargazers_count"] < MIN_STARS[level] or (level == "archived" and not repo["archived"]):
                continue
            since = str((p.get("extra") or {}).get(level if level != "archived" else "accepted", ""))[:10]
            items.append({"name": p["name"], "key": p["name"].lower(), "ring": RING[level], "quadrant": "Tools",
                          "what": repo.get("description") or p["name"], "basis": "formal",
                          "fact": f"{p['name']} is {MEANING[level]}" + (f" (since {since})." if since and level != "archived" else "."),
                          "source": "https://landscape.cncf.io/", "check": {"type": "cncf", "name": p["name"], "level": level},
                          "repo": path, "packages": {}, "search": [p["name"]]})
            taken_names.add(p["name"].lower())
            taken_repos.add(path.lower())
            got += 1
        print(f"{level:<10} -> {RING[level]:<6} drew {got}/{PER_LEVEL}: " + ", ".join(i["name"] for i in items[-got:]))
    for name, ring, quadrant, basis, what, fact, source, check, repo, packages in HAND:
        ok = requests.head(source, headers={"User-Agent": "Mozilla/5.0"}, timeout=25, allow_redirects=True).status_code
        print(f"{'hand':<10} -> {ring:<6} {name:<18} source answers {ok}" + ("" if ok == 200 else "   <-- CHECK THIS LINK"))
        items.append({"name": name, "key": name.lower(), "ring": ring, "quadrant": quadrant, "what": what, "basis": basis, "fact": fact, "source": source,
                      "check": check, "repo": repo, "packages": packages, "search": [name]})
    meta = {"drawn_with": f"eval/build_heldout.py, seed {SEED}", "how_to_read": __doc__.split("Run it once")[0].strip()}
    json.dump({"meta": meta, "items": items}, open(os.path.join(ROOT, "eval", "heldout_radar.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nwrote eval/heldout_radar.json: {len(items)} technologies, {sum(1 for i in items if i['ring'] == 'Adopt')} Adopt, "
          f"{sum(1 for i in items if i['ring'] == 'Trial')} Trial, {sum(1 for i in items if i['ring'] == 'Assess')} Assess, {sum(1 for i in items if i['ring'] == 'Hold')} Hold")


if __name__ == "__main__":
    main()
