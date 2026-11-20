"""Bind a single registered capital-stock class to same-filing common-stock facts."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup, Tag

from .historical_governance import _par_value
from .historical_identity import (
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    _soup,
    class_signature,
    instant,
)

_TITLE = "dei:security12btitle"
_SHARES = "dei:entitycommonstocksharesoutstanding"
_PAR = "us-gaap:commonstockparorstatedvaluepershare"


def _text(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def _only(tags: list[Tag], error: str) -> Tag:
    if len(tags) != 1:
        raise IdentityEvidenceError(error)
    return tags[0]


def _context(soup: BeautifulSoup, fact: Tag, cik: str) -> Tag:
    ref = fact.get("contextref")
    if not isinstance(ref, str) or not ref:
        raise IdentityEvidenceError("missing_capital_stock_context")
    context = _only(soup.find_all("xbrli:context", id=ref), "ambiguous_capital_stock_context")
    if [tag.name for tag in context.find_all(recursive=False)] != ["xbrli:entity", "xbrli:period"]:
        raise IdentityEvidenceError("unexpected_capital_stock_context")
    entity = _only(context.find_all("xbrli:entity"), "ambiguous_capital_stock_entity")
    children = entity.find_all(recursive=False)
    identifier = _only(children, "dimensioned_or_ambiguous_capital_stock_entity")
    if (
        identifier.name != "xbrli:identifier"
        or identifier.get("scheme") not in {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
        or _text(identifier) != cik
    ):
        raise IdentityEvidenceError("capital_stock_context_issuer_mismatch")
    return _only(context.find_all("xbrli:period"), "ambiguous_capital_stock_period")


def _instant(soup: BeautifulSoup, fact: Tag, cik: str) -> date:
    period = _context(soup, fact, cik)
    point = _only(period.find_all(recursive=False), "capital_stock_fact_requires_instant")
    if point.name != "xbrli:instant" or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}", _text(point)
    ):
        raise IdentityEvidenceError("invalid_capital_stock_fact_date")
    return date.fromisoformat(_text(point))


def _unit(soup: BeautifulSoup, fact: Tag) -> Tag:
    ref = fact.get("unitref")
    if not isinstance(ref, str) or not ref:
        raise IdentityEvidenceError("missing_capital_stock_unit")
    return _only(soup.find_all("xbrli:unit", id=ref), "ambiguous_capital_stock_unit")


def _number(fact: Tag, *, formats: set[str | None]) -> str:
    if (
        fact.get("scale") != "0"
        or fact.get("format") not in formats
        or any(fact.has_attr(name) for name in ("sign", "continuedat", "xsi:nil", "target"))
        or fact.find_parent(["ix:hidden", "ix:exclude"]) is not None
        or fact.find("ix:exclude") is not None
    ):
        raise IdentityEvidenceError("unsupported_capital_stock_numeric_fact")
    return _text(fact)


def qualify_tagged_capital_stock(
    payload: bytes, periodic: dict[str, Any], *, cutoff: datetime
) -> dict[str, Any]:
    """Require a single cover class, an outstanding share fact and a par-value row."""
    if (
        periodic.get("form") not in {"10-K", "10-Q"}
        or class_signature(periodic["security_title"]) != ("capital", "", "")
        or instant(periodic["available_by"]) > cutoff
        or hashlib.sha256(payload).hexdigest() != periodic["document_sha256"]
    ):
        raise IdentityEvidenceError("unbound_capital_stock_registration")
    registered_par = _par_value(periodic["security_title"])
    soup = _soup(payload, MAX_RETAINED_DOCUMENT_BYTES)
    if soup.html is None or any(
        not re.fullmatch(pattern, str(soup.html.get(prefix, "")))
        for prefix, pattern in {
            "xmlns:dei": r"http://xbrl\.sec\.gov/dei/20[0-9]{2}",
            "xmlns:us-gaap": r"http://fasb\.org/us-gaap/20[0-9]{2}",
            "xmlns:xbrli": r"http://www\.xbrl\.org/2003/instance",
            "xmlns:ix": r"http://www\.xbrl\.org/2013/inlineXBRL",
            "xmlns:iso4217": r"http://www\.xbrl\.org/2003/iso4217",
            "xmlns:ixt": r"http://www\.xbrl\.org/inlineXBRL/transformation/2020-02-12",
        }.items()
    ):
        raise IdentityEvidenceError("unsupported_capital_stock_namespaces")

    cik = periodic["issuer_cik"]
    titles = soup.find_all("ix:nonnumeric", attrs={"name": re.compile("^" + _TITLE + "$", re.I)})
    title = _only(titles, "capital_stock_registration_requires_single_class")
    if (
        _text(title) != periodic["security_title"]
        or title.find_parent("tr") is None
        or title.find_parent(["ix:hidden", "ix:exclude"]) is not None
        or title.has_attr("continuedat")
    ):
        raise IdentityEvidenceError("capital_stock_registration_title_mismatch")
    period = _context(soup, title, cik)
    period_end_tag = _only(period.find_all("xbrli:enddate"), "missing_capital_stock_report_date")
    period_end = date.fromisoformat(_text(period_end_tag))

    shares = _only(
        soup.find_all("ix:nonfraction", attrs={"name": re.compile("^" + _SHARES + "$", re.I)}),
        "capital_stock_outstanding_count_ambiguous",
    )
    shares_value = _number(shares, formats={"ixt:num-dot-decimal"})
    shares_date = _instant(soup, shares, cik)
    if not isinstance(shares.parent, Tag):
        raise IdentityEvidenceError("missing_capital_stock_cover_sentence")
    shares_sentence = _text(shares.parent)
    shares_label = re.fullmatch(
        rf"There were {re.escape(shares_value)} shares of capital stock outstanding as of "
        r"([A-Za-z]+ [0-9]{1,2}, [0-9]{4})\.",
        shares_sentence,
        re.I,
    )
    shares_unit = _unit(soup, shares).find_all(recursive=False)
    accepted = instant(periodic["accepted_at"]).date()
    if (
        shares_label is None
        or datetime.strptime(shares_label[1], "%B %d, %Y").date() != shares_date
        or not period_end <= shares_date <= accepted
        or not re.fullmatch(r"(?:[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)", shares_value)
        or shares.get("decimals") != "0"
        or [(unit.name, _text(unit)) for unit in shares_unit] != [("xbrli:measure", "xbrli:shares")]
    ):
        raise IdentityEvidenceError("capital_stock_outstanding_fact_mismatch")

    par_facts = soup.find_all("ix:nonfraction", attrs={"name": re.compile("^" + _PAR + "$", re.I)})
    if not 1 <= len(par_facts) <= 3:
        raise IdentityEvidenceError("missing_or_excessive_capital_stock_par_facts")
    row = par_facts[0].find_parent("tr")
    cell = par_facts[0].find_parent("td")
    if (
        row is None
        or cell is None
        or not re.match(r"^Capital stock,\s*\$\s*[0-9]*\.?[0-9]+\s+par value\b", _text(cell), re.I)
        or _par_value(_text(cell)) != registered_par
    ):
        raise IdentityEvidenceError("missing_capital_stock_balance_sheet_label")
    dates = set()
    for fact in par_facts:
        amount = _number(fact, formats={None, "ixt:num-dot-decimal"})
        point = _instant(soup, fact, cik)
        measure = _unit(soup, fact)
        if (
            fact.find_parent("tr") is not row
            or fact.find_parent("td") is not cell
            or not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", amount)
            or Decimal(amount) != registered_par
            or not re.fullmatch(r"[0-9]{1,2}", str(fact.get("decimals", "")))
            or Decimal(amount).normalize().as_tuple().exponent != -int(str(fact["decimals"]))
            or point > period_end
            or point in dates
            or [tag.name for tag in measure.find_all(True)]
            != [
                "xbrli:divide",
                "xbrli:unitnumerator",
                "xbrli:measure",
                "xbrli:unitdenominator",
                "xbrli:measure",
            ]
            or measure.select_one("xbrli\\:unitnumerator > xbrli\\:measure") is None
            or measure.select_one("xbrli\\:unitdenominator > xbrli\\:measure") is None
            or list(measure.stripped_strings) != ["iso4217:USD", "xbrli:shares"]
        ):
            raise IdentityEvidenceError("capital_stock_common_par_fact_mismatch")
        dates.add(point)
    if period_end not in dates:
        raise IdentityEvidenceError("missing_current_capital_stock_par_fact")

    return {
        "filing_sha256": periodic["document_sha256"],
        "registered_par_value_usd": str(registered_par),
        "common_stock_par_fact_dates": sorted(point.isoformat() for point in dates),
        "common_shares_count_date": shares_date.isoformat(),
        "capital_stock_row_sha256": hashlib.sha256(_text(row).encode()).hexdigest(),
        "capital_stock_cover_sentence_sha256": hashlib.sha256(
            _text(shares.parent).encode()
        ).hexdigest(),
    }
