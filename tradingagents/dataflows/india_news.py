"""India market news from public RSS feeds (Economic Times, Moneycontrol,
LiveMint, Hindu BusinessLine).

These feeds are free and require no API key. They give India-appropriate macro
+ market headlines for the news analyst, replacing the US-centric yfinance Search
(Fed/S&P) path for Indian tickers.

The single network boundary is ``_fetch_rss`` so the formatting/filtering logic is
unit-testable without network. RSS is parsed with ``parsel`` (already a dependency)
— no ``feedparser`` needed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

import requests
from dateutil.relativedelta import relativedelta
from parsel import Selector

from .config import get_config

logger = logging.getLogger(__name__)

# Free, public India market/business RSS feeds (no key required). Verified
# reachable with the browser headers below (2026-06). Business Standard is
# omitted — it returns 403 even with browser headers. Mix covers markets + macro.
_MARKET_FEEDS = [
    ("Economic Times Markets",
     "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Economic Times Economy",
     "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms"),
    ("Moneycontrol Business",
     "https://www.moneycontrol.com/rss/business.xml"),
    ("LiveMint Markets",
     "https://www.livemint.com/rss/markets"),
    ("Hindu BusinessLine Markets",
     "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
]

# A browser-like User-Agent + Accept is required: several Indian publishers
# return HTTP 403 to default/library agents.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
}


def _fetch_rss(url: str, timeout: int = 10) -> Optional[list[dict]]:
    """Fetch and parse one RSS feed into a list of article dicts.

    The only network call in this module. Returns ``None`` on failure (so the
    caller can tell "feed unreachable" from "feed reachable but empty") and a
    (possibly empty) list on success. Each item: title/summary/link/pub_date.
    """
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # network / HTTP / timeout
        logger.warning("India RSS fetch failed for %s: %s", url, exc)
        return None
    return _parse_rss(resp.text)


def _parse_rss(xml_text: str) -> list[dict]:
    """Parse RSS 2.0 XML text into article dicts (pure, testable)."""
    sel = Selector(text=xml_text, type="xml")
    items = []
    for it in sel.xpath("//item"):
        title = (it.xpath("./title/text()").get() or "").strip()
        summary = (it.xpath("./description/text()").get() or "").strip()
        link = (it.xpath("./link/text()").get() or "").strip()
        pub_raw = (it.xpath("./pubDate/text()").get() or "").strip()
        pub_date = None
        if pub_raw:
            try:
                pub_date = parsedate_to_datetime(pub_raw).replace(tzinfo=None)
            except (TypeError, ValueError):
                pass
        if title:
            items.append({
                "title": title, "summary": summary,
                "link": link, "pub_date": pub_date,
            })
    return items


def _collect(
    curr_dt: datetime,
    start_dt: datetime,
    drop_undated: bool = False,
) -> tuple[list[dict], int]:
    """Collect+dedupe articles from all feeds within [start_dt, curr_dt+1d].

    Returns ``(articles, reachable_feed_count)``. Articles after ``curr_dt`` are
    dropped (look-ahead guard for backtest fidelity). ``reachable_feed_count``
    lets callers fall back to another vendor when every feed is unreachable
    (vs. simply no news in the window).

    ``drop_undated``: RSS feeds only carry *current* items, so an article with no
    parseable ``pubDate`` cannot be placed in time. For a historical/backtest
    date these must be dropped, otherwise today's headlines leak into an old
    analysis date. For a live (today) run, undated items are kept — they are by
    definition current.
    """
    seen, out, reachable = set(), [], 0
    horizon = curr_dt + relativedelta(days=1)
    for source, url in _MARKET_FEEDS:
        fetched = _fetch_rss(url)
        if fetched is None:
            continue  # feed unreachable
        reachable += 1
        for art in fetched:
            title = art["title"]
            if title in seen:
                continue
            pub = art["pub_date"]
            if pub is None:
                if drop_undated:
                    continue  # can't time it; exclude from a historical window
            elif pub > horizon or pub < start_dt:
                continue
            seen.add(title)
            out.append({**art, "source": source})
    return out, reachable


def _is_historical(curr_dt: datetime) -> bool:
    """True when ``curr_dt`` is before today (i.e. a backtest/historical date)."""
    return curr_dt.date() < datetime.now().date()


def _format(header: str, articles: list[dict], limit: int) -> str:
    body = ""
    for art in articles[:limit]:
        body += f"### {art['title']} (source: {art['source']})\n"
        if art["summary"]:
            body += f"{art['summary']}\n"
        if art["link"]:
            body += f"Link: {art['link']}\n"
        body += "\n"
    return header + body


def get_global_news_india(
    curr_date: str,
    look_back_days: Optional[int] = None,
    limit: Optional[int] = None,
    drop_undated: Optional[bool] = None,
) -> str:
    """India macro/market news from RSS feeds (signature matches the router).

    ``drop_undated`` defaults to auto: undated items are dropped for a historical
    (backtest) date and kept for a live (today) run. Pass explicitly to override.
    """
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    if limit is None:
        limit = config["global_news_article_limit"]

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - relativedelta(days=look_back_days)
    if drop_undated is None:
        drop_undated = _is_historical(curr_dt)
    articles, reachable = _collect(curr_dt, start_dt, drop_undated)
    if reachable == 0:
        # Every feed was unreachable — let the router fall back to another vendor.
        raise RuntimeError("All India RSS feeds unreachable")
    if not articles:
        return f"No India market news found for {curr_date}"
    header = (f"## India Market News, from {start_dt.strftime('%Y-%m-%d')} "
              f"to {curr_date}:\n\n")
    return _format(header, articles, limit)


def get_news_india(
    ticker: str,
    start_date: str,
    end_date: str,
    drop_undated: Optional[bool] = None,
) -> str:
    """India ticker news: market-feed articles mentioning the company keyword.

    Best-effort — RSS feeds are market-wide, so this filters by the ticker's base
    symbol. Raises nothing for the no-match case; returns a "no news" string (the
    router keeps yfinance available as the per-ticker fallback). ``drop_undated``
    defaults to auto (historical → drop, live → keep).
    """
    config = get_config()
    limit = config["news_article_limit"]
    keyword = ticker.split(".")[0].strip().upper()  # RELIANCE.NS -> RELIANCE

    curr_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    if drop_undated is None:
        drop_undated = _is_historical(curr_dt)
    collected, reachable = _collect(curr_dt, start_dt, drop_undated)
    if reachable == 0:
        raise RuntimeError("All India RSS feeds unreachable")
    articles = [
        a for a in collected
        if keyword and keyword in f"{a['title']} {a['summary']}".upper()
    ]
    if not articles:
        return f"No India news found for {ticker} between {start_date} and {end_date}"
    header = f"## {ticker} India News, from {start_date} to {end_date}:\n\n"
    return _format(header, articles, limit)
