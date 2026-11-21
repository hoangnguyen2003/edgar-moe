"""Replay a fixed-cohort liquidity screen from independently reviewed archive inputs."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas_market_calendars as mcal

from edgar_moe.data.historical_identity import instant, valid_cusip
from edgar_moe.data.refresh import UniverseMember
from edgar_moe.data.universe import screen_liquid_universe
from edgar_moe.utils.time import NEW_YORK

_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,100}")
_ANCHORED = {"pre_cutoff_SEC_identity_anchored", "pre_cutoff_FDIC_SEC_identity_anchored"}


def _date(value: Any) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("historical_market_invalid_date")
    return date.fromisoformat(value)


def _number(value: Any, *, minimum: float, inclusive: bool = False) -> bool:
    return (
        type(value) in (int, float)
        and abs(value) <= 1e20
        and math.isfinite(value)
        and (value >= minimum if inclusive else value > minimum)
    )


@dataclass(frozen=True)
class Listing:
    cusip: str
    symbol: str
    start: date
    end: date

    def includes(self, session: date) -> bool:
        return self.start <= session < self.end


def _cohort(identity: dict[str, Any], cutoff: datetime) -> dict[str, UniverseMember]:
    cohort: dict[str, UniverseMember] = {}
    resolved: dict[str, dict[str, Any]] = {}
    for result in identity["results"]:
        key = result["snapshot_member_key"]
        if key in resolved or result["status"] not in _ANCHORED:
            raise ValueError("historical_market_incomplete_identity_replay")
        resolved[key] = result["anchor"]
    seen_symbols: set[str] = set()
    for source in identity["binding"]["members"]:
        anchor = resolved.pop(source["snapshot_member_key"], None)
        if anchor is None:
            raise ValueError("historical_market_incomplete_identity_replay")
        security_id = anchor["security_id"]
        symbol = source["symbol_at_snapshot"]
        if (
            not isinstance(security_id, str)
            or not security_id.startswith("cusip:")
            or not valid_cusip(security_id[6:])
            or security_id[6:] in cohort
            or not isinstance(symbol, str)
            or _SYMBOL.fullmatch(symbol) is None
            or symbol in seen_symbols
            or instant(anchor["available_by"]) > cutoff
            or anchor["issuer_cik"] != source["cik"]
            or anchor["symbol_at_cutoff"] != symbol
        ):
            raise ValueError("historical_market_identity_mismatch")
        cohort[security_id[6:]] = UniverseMember(
            cik=source["cik"], symbol=symbol, security_id=security_id
        )
        seen_symbols.add(symbol)
    if not cohort or resolved or len(cohort) != identity["binding"]["source_count"]:
        raise ValueError("historical_market_incomplete_identity_replay")
    return cohort


def _disjoint(periods: list[Listing]) -> None:
    periods.sort(key=lambda period: period.start)
    if any(
        previous.end > current.start
        for previous, current in zip(periods, periods[1:], strict=False)
    ):
        raise ValueError("historical_market_overlapping_listing_intervals")


def _listing_intervals(
    raw: Any, cohort: dict[str, UniverseMember], cutoff: datetime, screen_date: date
) -> dict[str, list[Listing]]:
    if not isinstance(raw, list) or not len(cohort) <= len(raw) <= 10 * len(cohort):
        raise ValueError("historical_market_listing_count")
    by_security: dict[str, list[Listing]] = defaultdict(list)
    by_symbol: dict[str, list[Listing]] = defaultdict(list)
    for row in raw:
        if not isinstance(row, dict) or set(row) != {
            "archive_security_id",
            "cusip",
            "symbol",
            "valid_from",
            "valid_before",
            "available_by",
        }:
            raise ValueError("historical_market_listing_fields")
        archive_id, cusip, symbol = (row[k] for k in ("archive_security_id", "cusip", "symbol"))
        start = _date(row["valid_from"])
        end = date.max if row["valid_before"] is None else _date(row["valid_before"])
        if (
            not isinstance(archive_id, str)
            or _ID.fullmatch(archive_id) is None
            or not isinstance(cusip, str)
            or cusip not in cohort
            or not isinstance(symbol, str)
            or _SYMBOL.fullmatch(symbol) is None
            or not start < end
            or start > screen_date
            or instant(row["available_by"]) > cutoff
        ):
            raise ValueError("historical_market_invalid_or_late_listing")
        period = Listing(cusip, symbol, start, end)
        by_security[archive_id].append(period)
        by_symbol[symbol].append(period)
    for periods in by_symbol.values():
        _disjoint(periods)
    matched: set[str] = set()
    for periods in by_security.values():
        _disjoint(periods)
        current = [period for period in periods if period.includes(screen_date)]
        cusips = {period.cusip for period in periods}
        if len(current) != 1 or len(cusips) != 1:
            raise ValueError("historical_market_ambiguous_security_continuity")
        issuer = current[0]
        if issuer.cusip in matched or issuer.symbol != cohort[issuer.cusip].symbol:
            raise ValueError("historical_market_cutoff_listing_mismatch")
        matched.add(issuer.cusip)
    if matched != set(cohort):
        raise ValueError("historical_market_missing_listing")
    return by_security


def _screen_bars(
    raw: Any,
    listings: dict[str, list[Listing]],
    cohort: dict[str, UniverseMember],
    lookback: date,
    cutoff: datetime,
    screen_date: date,
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(raw, dict) or set(raw) != set(listings):
        raise ValueError("historical_market_incomplete_bar_inputs")
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(start_date=lookback, end_date=screen_date)
    closes = {
        session.date(): close.to_pydatetime() for session, close in schedule["market_close"].items()
    }
    for archive_id, periods in listings.items():
        series = raw[archive_id]
        if not isinstance(series, list) or len(series) > (screen_date - lookback).days + 1:
            raise ValueError("historical_market_invalid_bar_count")
        days: set[date] = set()
        bars = []
        for bar in series:
            if not isinstance(bar, dict) or set(bar) != {
                "session",
                "symbol",
                "available_by",
                "close",
                "volume",
            }:
                raise ValueError("historical_market_invalid_bar_fields")
            day, available = _date(bar["session"]), instant(bar["available_by"])
            if (
                day in days
                or day not in closes
                or not closes[day] <= available <= cutoff
                or not _number(bar["close"], minimum=0)
                or not _number(bar["volume"], minimum=0, inclusive=True)
                or not any(
                    period.includes(day) and period.symbol == bar["symbol"] for period in periods
                )
            ):
                raise ValueError("historical_market_unbound_duplicate_or_late_bar")
            days.add(day)
            bars.append({"t": day.isoformat(), "c": bar["close"], "v": bar["volume"]})
        by_symbol[cohort[periods[0].cusip].symbol] = bars
    return by_symbol


def replay_historical_screen(
    identity: dict[str, Any], listings: Any, bars: Any, policy: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cutoff = instant(identity["binding"]["cutoff"])
    screen_date = cutoff.astimezone(NEW_YORK).date()
    lookback = _date(policy["lookback_start"])
    cohort = _cohort(identity, cutoff)
    maximum, sessions, price = (
        policy["maximum_issuers"],
        policy["minimum_sessions"],
        policy["minimum_price"],
    )
    if (
        not 1 <= (screen_date - lookback).days <= 366
        or type(maximum) is not int
        or not 1 <= maximum <= len({member.cik for member in cohort.values()})
        or type(sessions) is not int
        or not 1 <= sessions <= (screen_date - lookback).days + 1
        or not _number(price, minimum=0)
    ):
        raise ValueError("historical_market_screen_policy")
    intervals = _listing_intervals(listings, cohort, cutoff, screen_date)
    normalized = _screen_bars(bars, intervals, cohort, lookback, cutoff, screen_date)
    ranked, counts = screen_liquid_universe(
        list(cohort.values()),
        normalized,
        candidate_count=len(cohort),
        minimum_sessions=sessions,
        minimum_price=price,
        as_of=screen_date,
    )
    issuers: set[str] = set()
    selected: list[dict[str, Any]] = []
    counts["additional_share_class"] = 0
    for item in ranked:
        if item["cik"] in issuers:
            counts["additional_share_class"] += 1
            continue
        issuers.add(item["cik"])
        if len(selected) < maximum:
            selected.append(item | {"screen_liquidity_rank": len(selected) + 1})
    if not selected:
        raise ValueError("historical_market_empty_selection")
    counts["below_candidate_cutoff"] = len(issuers) - len(selected)
    counts["selected"] = len(selected)
    return selected, counts
