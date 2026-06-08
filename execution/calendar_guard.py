"""Calendar readiness guard — Phase 3 build-step-0 blocker.

A daily paper/live run must never start against an unpopulated or implausibly
short NSE holiday list. The built-in fallback in
:mod:`tradingagents.dataflows.india_calendar` is *conservative by design* (only a
few certain fixed-date holidays), which is safe for analysis but unsafe for a
trading loop: it would treat real movable holidays (Holi, Diwali, Good Friday,
Eid, ...) as open trading days.

:func:`assert_calendar_ready` is called at the start of every execution pass
(see :mod:`execution.router`). It **refuses to run** unless ``config["nse_holidays"]``
is populated for the year being traded, with a plausible count and the known
fixed-date anchors present. Fail-closed: when in doubt, don't trade.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

# Minimum plausible number of weekday closures in an NSE calendar year. The real
# count is ~14-16; a configured year with fewer than this is almost certainly a
# partial/placeholder list and must not be traded against.
MIN_HOLIDAYS_PER_YEAR = 10

# Fixed-date national holidays that fall on the same Gregorian date every year and
# are (almost) always weekday closures. Their presence is a cheap sanity check that
# the configured list is a real NSE calendar and not an unrelated set of dates.
# (month, day). Republic Day, Gandhi Jayanti, Christmas.
_FIXED_DATE_ANCHORS = ((1, 26), (10, 2), (12, 25))


class CalendarNotConfigured(RuntimeError):
    """Raised when the NSE holiday calendar for the trading year is not usable."""


def _holidays_for_year(holidays: Iterable[str], year: int) -> set[str]:
    """Return the subset of ``YYYY-MM-DD`` holiday strings that fall in ``year``.

    Each entry must parse as an ISO date; a malformed entry is itself a
    misconfiguration and raises (fail-closed — we don't silently skip junk).
    """
    prefix = f"{year:04d}-"
    out: set[str] = set()
    for h in holidays:
        s = str(h).strip()
        if not s:
            continue
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError as exc:
            raise CalendarNotConfigured(
                f"nse_holidays contains a non-ISO date entry: {s!r}"
            ) from exc
        if s.startswith(prefix):
            out.add(s)
    return out


def assert_calendar_ready(
    config: dict,
    year: int,
    *,
    holidays: Optional[Iterable[str]] = None,
) -> None:
    """Raise :class:`CalendarNotConfigured` unless the calendar for ``year`` is usable.

    Usable means: the configured ``nse_holidays`` list (or the ``holidays`` override)
    contains at least :data:`MIN_HOLIDAYS_PER_YEAR` entries for ``year`` **and** every
    fixed-date anchor (Republic Day / Gandhi Jayanti / Christmas) for that year. This
    is a deliberately loud, fail-closed startup check — not a substitute for the
    authoritative NSE list, but a guarantee that *some* full year list was loaded.
    """
    source = holidays if holidays is not None else (config or {}).get("nse_holidays")
    if not source:
        raise CalendarNotConfigured(
            f"nse_holidays is empty — populate the full official NSE {year} trading "
            "holiday list before running a daily execution pass (see default_config "
            "and niranjan_docs/13-phase3-build-plan.md, Task 0)."
        )

    year_holidays = _holidays_for_year(source, year)
    if len(year_holidays) < MIN_HOLIDAYS_PER_YEAR:
        raise CalendarNotConfigured(
            f"nse_holidays has only {len(year_holidays)} entr"
            f"{'y' if len(year_holidays) == 1 else 'ies'} for {year} "
            f"(need >= {MIN_HOLIDAYS_PER_YEAR}). This looks like a partial/placeholder "
            "list — refusing to trade against an incomplete calendar."
        )

    missing_anchors = [
        f"{year:04d}-{m:02d}-{d:02d}"
        for (m, d) in _FIXED_DATE_ANCHORS
        if f"{year:04d}-{m:02d}-{d:02d}" not in year_holidays
    ]
    if missing_anchors:
        raise CalendarNotConfigured(
            f"nse_holidays for {year} is missing expected fixed-date holiday(s): "
            f"{', '.join(missing_anchors)}. Verify the list against the official NSE "
            "calendar before trading."
        )
