"""News scraper (RSS) for the sentiment pipeline.

Pulls every feed configured under ``sentiment.feeds`` plus Google News RSS
search queries. Articles are appended to a cumulative store
(``data/raw/news/articles.csv``) and de-duplicated by link, so repeated
pipeline runs build up history over time — RSS only exposes recent items,
which is an inherent limitation of free news sources.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlencode

import feedparser
import pandas as pd

from ..config import Config
from ..utils.http_utils import HttpClient
from ..utils.logging_utils import get_logger

log = get_logger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html.unescape(text))).strip()


def _parse_feed(http: HttpClient, url: str, source: str, lang: str) -> list[dict]:
    try:
        resp = http.get(url)
        if resp.status_code != 200:
            log.info("feed %s -> HTTP %d, skipped", url, resp.status_code)
            return []
        parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001 - dead feeds must not kill the run
        log.info("feed %s unavailable: %s", url, exc)
        return []
    rows = []
    for entry in parsed.entries:
        published = None
        for key in ("published_parsed", "updated_parsed"):
            t = entry.get(key)
            if t:
                published = pd.Timestamp(*t[:6])
                break
        rows.append({
            "source": source,
            "feed_url": url,
            "lang": lang,
            "title": _clean_text(entry.get("title")),
            "summary": _clean_text(entry.get("summary") or entry.get("description")),
            "link": (entry.get("link") or "").strip(),
            "published": published,
        })
    log.info("feed %s: %d entries", url, len(rows))
    return rows


def run(cfg: Config) -> pd.DataFrame:
    http = HttpClient(cfg)
    rows: list[dict] = []

    for feed in cfg.get("sentiment.feeds", []):
        rows.extend(_parse_feed(http, str(feed["url"]), str(feed.get("source", "rss")),
                                str(feed.get("lang", "en"))))

    gn = cfg.get("sentiment.google_news", {}) or {}
    base = gn.get("base_url")
    if base:
        params = dict(gn.get("params", {}))
        for query in gn.get("queries", []):
            url = f"{base}?{urlencode({'q': query, **params})}"
            batch = _parse_feed(http, url, "google_news", "en")
            for row in batch:
                row["query"] = query
            rows.extend(batch)

    fresh = pd.DataFrame(rows)
    path = cfg.output_path("raw_news")
    if path.exists():
        existing = pd.read_csv(path, parse_dates=["published", "fetched_at"])
    else:
        existing = pd.DataFrame()

    if not fresh.empty:
        fresh["published"] = pd.to_datetime(fresh["published"], errors="coerce")
        fresh["published"] = fresh["published"].fillna(pd.Timestamp.now().normalize())
        fresh["fetched_at"] = pd.Timestamp.now()
        fresh = fresh[fresh["title"].str.len() > 0]

    combined = pd.concat([existing, fresh], ignore_index=True) if not fresh.empty else existing
    if combined.empty:
        log.warning("no news articles collected")
        combined = pd.DataFrame(columns=["source", "feed_url", "lang", "title", "summary",
                                         "link", "published", "fetched_at"])
    else:
        combined["dedupe_key"] = combined["link"].where(
            combined["link"].astype(str).str.len() > 0,
            combined["source"].astype(str) + "|" + combined["title"].astype(str),
        )
        combined = (combined.sort_values("fetched_at")
                    .drop_duplicates(subset="dedupe_key", keep="first")
                    .drop(columns="dedupe_key")
                    .sort_values("published")
                    .reset_index(drop=True))
    combined.to_csv(path, index=False)
    log.info("articles store: %d total (%d new this run) -> %s",
             len(combined), len(combined) - len(existing), path)
    return combined
