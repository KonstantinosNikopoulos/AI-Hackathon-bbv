"""RSS / Atom feeds: tech blogs and news sites. Uses only the standard library to parse."""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from config import AREAS, clamp_days


def _clean(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)          # RSS: "Tue, 30 Sep 2026 10:00:00 GMT"
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))   # Atom: ISO 8601
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(xml_text):
    """Returns [{title, link, date, summary}] for RSS 2.0, RSS 1.0 (RDF) and Atom."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    entries = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        fields = {"title": "", "link": "", "date": "", "summary": ""}
        for child in el:
            name = _local(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                fields["title"] = text
            elif name == "link":
                href = child.attrib.get("href")
                rel = child.attrib.get("rel", "alternate")
                if href and rel == "alternate":
                    fields["link"] = href
                elif text and not fields["link"]:
                    fields["link"] = text
            elif name in ("pubdate", "published", "updated", "date") and not fields["date"]:
                fields["date"] = text
            elif name in ("description", "summary") and not fields["summary"]:
                fields["summary"] = text
            elif name in ("content", "encoded") and not fields["summary"]:
                fields["summary"] = text
        entries.append(fields)
    return entries


def get_rss_items(feeds, per_feed=5, technology_area="All", days=30, errors=None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=clamp_days(days))
    keywords = AREAS.get(technology_area, AREAS["All"])["keywords"]
    items = []
    for url in feeds:
        try:
            response = requests.get(url, timeout=15, headers={"User-Agent": "bbv-tech-radar"})
            response.raise_for_status()
            entries = parse_feed(response.content)
        except Exception as error:  # one broken feed must not stop the scan
            if errors is not None:
                errors.append(f"RSS {url}: {error}")
            continue
        count = 0
        for e in entries:
            d = _parse_date(e["date"])
            if d and d < cutoff:
                continue
            title, summary = _clean(e["title"]), _clean(e["summary"])
            if not title or not e["link"]:
                continue
            if keywords and not any(re.search(r"\b" + re.escape(k) + r"\b", f"{title} {summary}", re.IGNORECASE) for k in keywords):
                continue
            items.append({
                "source": "RSS feeds",
                "group": urlparse(e["link"]).netloc.replace("www.", "") or "rss",
                "title": title,
                "url": e["link"],
                "text": summary[:300],
                "date": d.strftime("%Y-%m-%d") if d else "",
                "meta": {"feed": url},
            })
            count += 1
            if count >= per_feed:
                break
    return items
