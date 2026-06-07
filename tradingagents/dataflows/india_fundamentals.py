"""India fundamentals enrichment via screener.in (Phase 2D, light).

screener.in is the gold standard for Indian retail fundamentals but has **no API**
— this scrapes the public company page for the key-ratio block + Pros/Cons, which
is what screener is uniquely good at. It is layered ON TOP of the existing yfinance
fundamentals (not a replacement): the returned text is ``screener summary + yfinance
overview``. If screener is unreachable or its DOM changes, we silently fall back to
yfinance alone, so this never degrades below the current behaviour.

ToS note: scraping is tolerated for personal/research use with light, cached access;
do not redistribute. Keep request volume low.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests
from parsel import Selector

from .y_finance import get_fundamentals as get_yfinance_fundamentals

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _parse_screener(html: str) -> Optional[str]:
    """Extract name + top ratios + Pros/Cons from a screener company page.

    Returns a formatted block, or ``None`` if the page yielded nothing usable
    (DOM changed / wrong page), so the caller can fall back to yfinance.
    """
    sel = Selector(text=html)
    name = (sel.css("h1::text").get() or "").strip()

    ratios = []
    for li in sel.css("#top-ratios li"):
        label = " ".join(t.strip() for t in li.css(".name::text").getall() if t.strip())
        value = " ".join(
            t.strip() for t in li.css(".value::text, .number::text").getall() if t.strip()
        )
        value = " ".join(value.split())
        if label and value:
            ratios.append(f"- {label}: {value}")

    def _list(css: str) -> list[str]:
        out = []
        for li in sel.css(css):
            txt = " ".join(t.strip() for t in li.css("::text").getall() if t.strip())
            txt = " ".join(txt.split())
            if txt:
                out.append(txt)
        return out

    pros = _list(".pros li")
    cons = _list(".cons li")

    if not (ratios or pros or cons):
        return None

    block = f"## screener.in — {name or 'company'} (India fundamentals)\n\n"
    if ratios:
        block += "### Key ratios\n" + "\n".join(ratios) + "\n\n"
    if pros:
        block += "### Pros\n" + "\n".join(f"- {p}" for p in pros) + "\n\n"
    if cons:
        block += "### Cons\n" + "\n".join(f"- {c}" for c in cons) + "\n\n"
    return block


def _fetch_screener(symbol: str) -> Optional[str]:
    """Best-effort screener fetch (consolidated first, then standalone)."""
    for path in (f"{symbol}/consolidated/", f"{symbol}/"):
        url = f"https://www.screener.in/company/{path}"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=12)
            if resp.status_code != 200:
                continue
            block = _parse_screener(resp.text)
            if block:
                return block
        except Exception as exc:  # network / parse
            logger.warning("screener.in fetch failed for %s: %s", symbol, exc)
    return None


def get_fundamentals_india(ticker: str, curr_date: str = None) -> str:
    """India fundamentals = screener.in summary (best-effort) + yfinance overview.

    Matches the ``get_fundamentals`` vendor signature. screener enrichment is
    additive and fail-soft; yfinance remains the backbone. Raises only if BOTH
    sources yield nothing (so the router can try a further vendor).
    """
    symbol = ticker.split(".")[0].strip().upper()  # RELIANCE.NS -> RELIANCE
    screener_block = _fetch_screener(symbol)

    yf_block = None
    try:
        yf_block = get_yfinance_fundamentals(ticker, curr_date)
    except Exception as exc:
        logger.warning("yfinance fundamentals failed for %s: %s", ticker, exc)

    if screener_block and yf_block:
        return screener_block + "\n" + yf_block
    if yf_block:
        return yf_block
    if screener_block:
        return screener_block
    # Both empty — re-raise so route_to_vendor falls through to another vendor.
    raise RuntimeError(f"No India fundamentals for {ticker} (screener + yfinance empty)")
