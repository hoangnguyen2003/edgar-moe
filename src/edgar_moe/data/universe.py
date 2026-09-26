from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from statistics import median
from typing import Any

from edgar_moe.data.refresh import UniverseMember

FUND_MARKERS = (
    " INDEX SHARES",
    "ISHARES",
    "SPDR ",
    "PROSHARES",
    "DIREXION",
    "WISDOMTREE",
    "GLOBAL X ",
    "INVESCO QQQ",
    "TRUST, SERIES",
)
FUND_TERMS = re.compile(r"\b(?:ETF|FUND|FUNDS|PORTFOLIO|PORTFOLIOS)\b")


def is_probable_fund(company_name: str) -> bool:
    normalized = f" {company_name.upper().strip()} "
    return bool(FUND_TERMS.search(normalized)) or any(
        marker in normalized for marker in FUND_MARKERS
    )


def screen_liquid_universe(
    members: Sequence[UniverseMember],
    bars: dict[str, list[dict[str, Any]]],
    *,
    candidate_count: int,
    minimum_sessions: int,
    minimum_price: float,
    as_of: date | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rank a tractable candidate set by trailing observed dollar volume."""
    if candidate_count < 1 or minimum_sessions < 1 or minimum_price <= 0:
        raise ValueError("Screen parameters must be positive")
    exclusions: Counter[str] = Counter()
    eligible: list[dict[str, Any]] = []
    for member in members:
        if is_probable_fund(member.company_name or ""):
            exclusions["fund_or_etp"] += 1
            continue
        observations: list[tuple[str, float, float]] = []
        for row in bars.get(member.symbol, []):
            timestamp = str(row.get("t", row.get("timestamp", "")))
            if as_of is not None and timestamp:
                try:
                    observed_date = date.fromisoformat(timestamp[:10])
                except ValueError as exc:
                    raise ValueError("Screen bar has an invalid date") from exc
                if observed_date > as_of:
                    raise ValueError("Screen bars extend beyond the declared cutoff")
            try:
                raw_close = row.get("c", row.get("close"))
                raw_volume = row.get("v", row.get("volume"))
                if raw_close is None or raw_volume is None:
                    continue
                close = float(raw_close)
                volume = float(raw_volume)
            except (TypeError, ValueError):
                continue
            if (
                timestamp
                and math.isfinite(close)
                and close > 0
                and math.isfinite(volume)
                and volume >= 0
            ):
                observations.append((timestamp, close, close * volume))
        observations.sort(key=lambda item: item[0])
        if len(observations) < minimum_sessions:
            exclusions["insufficient_sessions"] += 1
            continue
        last_price = observations[-1][1]
        if last_price < minimum_price:
            exclusions["minimum_price"] += 1
            continue
        eligible.append(
            {
                **member.model_dump(mode="json"),
                "screen_sessions": len(observations),
                "screen_last_price": round(last_price, 6),
                "screen_median_dollar_volume": round(median(item[2] for item in observations), 2),
            }
        )
    eligible.sort(key=lambda item: (-float(item["screen_median_dollar_volume"]), item["symbol"]))
    selected = eligible[:candidate_count]
    for rank, item in enumerate(selected, start=1):
        item["screen_liquidity_rank"] = rank
    exclusions["below_candidate_cutoff"] = max(0, len(eligible) - len(selected))
    exclusions["selected"] = len(selected)
    return selected, dict(exclusions)
