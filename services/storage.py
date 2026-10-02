"""Saves every scan as JSON in data/runs, so the demo is instant and runs can be compared."""
import glob
import json
import os

from config import DATA_DIR, RINGS
from services.pipeline import tech_key


def save_run(result):
    os.makedirs(DATA_DIR, exist_ok=True)
    name = result["run_at"].replace(":", "-") + ".json"
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return path


def list_runs():
    """Newest first: [(path, label)]."""
    paths = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")), reverse=True)
    runs = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                r = json.load(f)
            s = r.get("settings", {})
            label = (f"{r['run_at'].replace('T', ' ')} · {s.get('area', '?')} · {s.get('model', '?')} · "
                     f"{len([t for t in r.get('technologies', []) if not t.get('is_seed')])} technologies")
        except Exception:
            label = os.path.basename(p)
        runs.append((p, label))
    return runs


def previous_run(result):
    """The newest saved run before this one with the same technology area (for 'what changed')."""
    for path, _ in list_runs():
        try:
            r = load_run(path)
        except Exception:
            continue
        if r["run_at"] < result["run_at"] and r.get("settings", {}).get("area") == result["settings"].get("area"):
            return r
    return None


def load_run(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare(previous, current):
    """Status per technology name, using the Thoughtworks words: New, Moved In, Moved Out, No Change."""
    if not previous:
        return {t["name"]: ("" if t.get("is_seed") else "New") for t in current["technologies"]}
    before = {tech_key(t["name"]): t for t in previous.get("technologies", [])}
    statuses = {}
    for t in current["technologies"]:
        old = before.get(tech_key(t["name"]))
        if old is None:
            statuses[t["name"]] = "New"
        elif old["ring"] == t["ring"]:
            statuses[t["name"]] = "No Change"
        elif RINGS.index(t["ring"]) < RINGS.index(old["ring"]):
            statuses[t["name"]] = "Moved In"
        else:
            statuses[t["name"]] = "Moved Out"
    return statuses
