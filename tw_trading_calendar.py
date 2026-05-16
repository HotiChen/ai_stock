"""
tw_trading_calendar.py — Taiwan Stock Exchange (TWSE) trading-day helper.

Provides a minimal, test-friendly holiday calendar so ``is_trading_day()``
in ``main.py`` can exclude national holidays, not just weekends.

Maintenance
-----------
Update ``_TWSE_HOLIDAYS`` every year, ideally before the last trading day of
the preceding year.  Official source:
  https://www.twse.com.tw/zh/news/notice  (搜尋「休市」)

Only weekday holidays need to be listed here — Saturdays and Sundays are
already excluded by the ``weekday() >= 5`` check in ``is_trading_day()``.

Scope
-----
This module exists *only* to support trading-day judgement.  Do not add
unrelated helpers here.
"""

from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# Holiday data — keyed by year, values are frozensets of weekday dates only.
# Dates that fall on Sat/Sun are omitted (weekday check handles them).
# ---------------------------------------------------------------------------
_TWSE_HOLIDAYS: dict[int, frozenset[date]] = {

    # ── 2026 ──────────────────────────────────────────────────────────────
    # Confidence levels are noted; update from official TWSE announcement
    # once it is published (typically November of the preceding year).
    2026: frozenset({
        # 元旦 (New Year's Day) — Thu — CERTAIN
        date(2026, 1, 1),

        # 農曆春節 (Chinese New Year, 農曆初一 = 2026-02-17) — CERTAIN range
        date(2026, 2, 16),   # 除夕     Mon
        date(2026, 2, 17),   # 初一     Tue
        date(2026, 2, 18),   # 初二     Wed
        date(2026, 2, 19),   # 初三     Thu
        date(2026, 2, 20),   # 初四     Fri

        # 二二八和平紀念日 (228 Peace Memorial Day) — 02/28 falls on Sat
        # → compensatory day off on following Monday
        date(2026, 3, 2),    # 228 補假  Mon — LIKELY

        # 兒童節 + 清明節 (Children's Day 04/04 Sat + Tomb Sweeping 04/05 Sun)
        # → Taiwan government typically adds Fri 04/03 and Mon 04/06
        date(2026, 4, 3),    # 清明/兒童節連假  Fri — LIKELY
        date(2026, 4, 6),    # 清明補假         Mon — LIKELY

        # 勞動節 (Labor Day) — Fri — CERTAIN
        date(2026, 5, 1),

        # 國慶日 (National Day) — 10/10 falls on Sat
        # → compensatory day off on preceding Friday
        date(2026, 10, 9),   # 國慶補假  Fri — LIKELY

        # TODO: 端午節、中秋節 — add once TWSE publishes the official 2026 calendar.
        # Estimated: Dragon Boat ~Jun 19-22, Mid-Autumn ~Sep 25 or Oct 1.
    }),
}

# ---------------------------------------------------------------------------
# Years whose calendar entry exists but is NOT yet fully confirmed.
# These years are in _TWSE_HOLIDAYS, but some holidays are still marked
# TODO/LIKELY.  ``is_calendar_complete()`` returns False for these years;
# callers should warn operators that a few trading-day decisions may be wrong.
# ---------------------------------------------------------------------------
_INCOMPLETE_CALENDAR_YEARS: frozenset[int] = frozenset({
    2026,  # 端午節 / 中秋節 pending TWSE official announcement
})


def is_year_in_calendar(year: int) -> bool:
    """Return ``True`` if *year* is covered by the built-in holiday calendar.

    Use this to distinguish "year is known but has no extra holidays" from
    "year is unknown → weekday-only fallback in effect".  The two cases look
    identical to ``is_twse_holiday()``, which silently returns ``False`` for
    both; callers that want to warn should check this first.
    """
    return year in _TWSE_HOLIDAYS


def is_calendar_complete(year: int) -> bool:
    """Return ``True`` only if the holiday calendar for *year* is fully confirmed.

    A year can be *in* the calendar (``is_year_in_calendar`` returns True) yet
    still be incomplete — meaning some holidays are flagged as TODO/LIKELY and
    have not yet been officially announced by TWSE.  On those missing dates the
    system will incorrectly treat a public holiday as a trading day.

    Use this to issue operator warnings when running in an incomplete year, so
    the degradation is visible rather than silent.
    """
    return year in _TWSE_HOLIDAYS and year not in _INCOMPLETE_CALENDAR_YEARS


def is_twse_holiday(d: date) -> bool:
    """Return ``True`` if *d* is a TWSE public holiday (weekday holidays only).

    Weekends are NOT checked here; that is handled by the ``weekday() >= 5``
    guard in ``is_trading_day()`` so the two concerns stay separate.

    Returns ``False`` for years not yet in ``_TWSE_HOLIDAYS`` rather than
    raising, so the scheduler degrades safely to weekday-only logic.
    """
    return d in _TWSE_HOLIDAYS.get(d.year, frozenset())
