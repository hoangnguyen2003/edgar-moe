"""Dated SEC evidence for a fixed cohort's issuer/share-class/CUSIP join.

This module is offline. A source row, current ticker or current asset UUID is
never sufficient. Regulatory publication timestamps and exact document bytes
must accompany both legs of the join. It does not authenticate a caller's
source files or authorize research/publication.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup, Tag

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_RETAINED_DOCUMENT_BYTES = 64 * 1024 * 1024
PUBLICATION_LAG_DAYS = 7
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
_NAMES = {
    "dei:entitycentralindexkey",
    "dei:tradingsymbol",
    "dei:securityexchangename",
    "dei:security12btitle",
}


class IdentityEvidenceError(ValueError):
    """Static refusal code, suitable for an operational report without rows."""


class RegistrationCoverExhibit(IdentityEvidenceError):
    """The primary filing explicitly delegates its registered-security cover."""

    def __init__(self, relative_href: str, primary_sha256: str):
        super().__init__("explicit_registration_cover_exhibit_required")
        self.relative_href = relative_href
        self.primary_sha256 = primary_sha256


def instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise IdentityEvidenceError("invalid_source_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise IdentityEvidenceError("invalid_source_timestamp") from error
    if parsed.utcoffset() is None:
        raise IdentityEvidenceError("naive_source_timestamp")
    return parsed.astimezone(UTC)


def source_metadata(
    metadata: dict[str, Any], *, cik: str, cutoff: datetime, forms: set[str]
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9]{10}", cik) is None or int(cik) == 0:
        raise IdentityEvidenceError("invalid_issuer_identity")
    if (
        metadata.get("cik") != cik
        or metadata.get("form") not in forms
        or not isinstance(metadata.get("accessionNumber"), str)
        or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", metadata["accessionNumber"]) is None
    ):
        raise IdentityEvidenceError("filing_metadata_identity_mismatch")
    accepted = instant(metadata.get("acceptanceDateTime"))
    available = accepted + timedelta(days=PUBLICATION_LAG_DAYS)
    if cutoff.utcoffset() is None or available > cutoff:
        raise IdentityEvidenceError("filing_not_available_by_cohort_cutoff")
    return {
        "issuer_cik": cik,
        "form": metadata["form"],
        "accession": metadata["accessionNumber"],
        "accepted_at": accepted.isoformat(),
        "available_by": available.isoformat(),
        "availability_basis": "SEC_reported_acceptance_plus_seven_calendar_days",
    }


def _soup(payload: bytes, maximum: int = MAX_DOCUMENT_BYTES) -> BeautifulSoup:
    if maximum not in {MAX_DOCUMENT_BYTES, MAX_RETAINED_DOCUMENT_BYTES}:
        raise IdentityEvidenceError("invalid_document_byte_bound")
    if not isinstance(payload, bytes) or not 0 < len(payload) <= maximum:
        raise IdentityEvidenceError("empty_or_excessive_document")
    return BeautifulSoup(payload, "html.parser")


def class_signature(value: str) -> tuple[str, str, str]:
    """Conservative class equivalence, ignoring par-value amounts, not class rights."""
    text = " ".join(value.upper().split())
    if re.search(
        r"\b(PREFERRED|PREFERENCE|DEPOSITARY|DEPOSITORY|WARRANTS?|NOTES?|BONDS?|UNITS?)\b", text
    ):
        raise IdentityEvidenceError("unsupported_security_class")
    families = {
        family
        for family, pattern in {
            "common": r"\bCOMMON\s+(?:STOCK|SHARES)\b",
            "ordinary": r"\bORDINARY\s+(?:STOCK|SHARES)\b",
            "capital": r"\bCAPITAL\s+STOCK\b",
            "registered": r"\bREGISTERED\s+SHARES\b",
        }.items()
        if re.search(pattern, text)
    }
    # A literal cover-field shorthand, not a narrative mention of "common".
    if re.fullmatch(r"COMMON\s*,\s*\$\s*\d+(?:\.\d+)?\s+PAR\s+VALUE", text):
        families.add("common")
    if len(families) != 1:
        raise IdentityEvidenceError("ambiguous_security_class")
    family = next(iter(families))
    classes = set(re.findall(r"\bCLASS\s+([A-Z0-9]+)\b", text))
    if not family or len(classes) > 1:
        raise IdentityEvidenceError("ambiguous_security_class")
    voting = "nonvoting" if re.search(r"\bNON[ -]?VOTING\b", text) else ""
    if not voting and re.search(r"\bVOTING\b", text):
        voting = "voting"
    return family, next(iter(classes), ""), voting


def _symbol_matches(value: str, expected: str, title: str | None = None) -> bool:
    """Literal cover typography, plus an explicitly matching dotted class suffix."""
    text = " ".join(value.split())
    for opening, closing in (("\u201c", "\u201d"), ('"', '"')):
        if text.startswith(opening) and text.endswith(closing):
            text = text[1:-1]
            break
    if text in {expected, expected + " (NYSE)"}:
        return True
    dotted = re.fullmatch(r"([A-Z]+)\.([A-Z])", expected)
    if dotted is None or text != dotted[1] + dotted[2] or title is None:
        return False
    try:
        return class_signature(title)[1] == dotted[2]
    except IdentityEvidenceError:
        return False


def periodic_anchor(
    payload: bytes,
    metadata: dict[str, Any],
    *,
    cik: str,
    symbol: str,
    cutoff: datetime,
    maximum_document_bytes: int = MAX_DOCUMENT_BYTES,
    shared_primary_document: bytes | None = None,
    shared_cover_href: str | None = None,
) -> dict[str, Any]:
    source = source_metadata(metadata, cik=cik, cutoff=cutoff, forms={"10-K", "10-Q"})
    if _SYMBOL.fullmatch(symbol) is None:
        raise IdentityEvidenceError("invalid_snapshot_symbol")
    soup = _soup(payload, maximum_document_bytes)
    grouped: dict[str, dict[str, set[str]]] = {}
    issuers: set[str] = set()
    count = 0
    for tag in soup.find_all("ix:nonnumeric"):
        name = str(tag.get("name", "")).lower()
        if name not in _NAMES:
            continue
        count += 1
        if count > 1024:
            raise IdentityEvidenceError("excessive_cover_facts")
        value = " ".join(tag.get_text(" ", strip=True).split())
        context = str(tag.get("contextref", ""))
        if name == "dei:entitycentralindexkey":
            if not re.fullmatch(r"[0-9]{1,10}", value):
                raise IdentityEvidenceError("invalid_document_issuer")
            issuers.add(value.zfill(10))
        if context and value:
            grouped.setdefault(context, {}).setdefault(name, set()).add(value)
    shared_binding: dict[str, Any] = {}
    if shared_primary_document is not None:
        if shared_cover_href is None or (issuers and issuers != {cik}):
            raise IdentityEvidenceError("invalid_shared_registration_issuer")
        shared_binding = _add_shared_registration_contexts(
            soup, grouped, shared_primary_document, shared_cover_href, cik, symbol, cutoff
        )
    context_issuers: dict[str, set[str]] = {}
    for context_tag in soup.find_all("xbrli:context"):
        context_id = str(context_tag.get("id", ""))
        identifiers = context_tag.find_all("xbrli:identifier")
        for identifier in identifiers:
            if identifier.get("scheme") not in {
                "http://www.sec.gov/CIK",
                "https://www.sec.gov/CIK",
            }:
                continue
            value = identifier.get_text(strip=True)
            if not re.fullmatch(r"[0-9]{1,10}", value):
                raise IdentityEvidenceError("invalid_context_issuer")
            context_issuers.setdefault(context_id, set()).add(value.zfill(10))
    if not context_issuers and issuers != {cik}:
        raise IdentityEvidenceError("document_issuer_mismatch")
    matches: set[tuple[str, str]] = set()
    supported_contexts: set[str] = set()
    reported_symbols: set[str] = set()
    exchange: str | None
    ambiguous = False
    for context_id, fields in grouped.items():
        symbols = fields.get("dei:tradingsymbol", set())
        titles = fields.get("dei:security12btitle", set())
        if len(symbols) != 1 or not _symbol_matches(
            next(iter(symbols)), symbol, next(iter(titles)) if len(titles) == 1 else None
        ):
            continue
        reported_symbols.update(symbols)
        # Combined reports may contain DEI facts for several registrants.
        # Bind the requested share-class facts to their own issuer context.
        if context_issuers and context_issuers.get(context_id) != {cik}:
            raise IdentityEvidenceError("symbol_context_issuer_mismatch")
        exchanges = fields.get("dei:securityexchangename", set())
        if len(titles) != 1 or len(exchanges) != 1:
            ambiguous = True
            continue
        title, exchange = next(iter(titles)), next(iter(exchanges))
        # Some covers give debt its own complete context but repeat the equity
        # ticker. Never merge its facts into equity or treat debt as a class
        # alias. Unknown/equity-like titles remain refusals.
        if re.fullmatch(
            r"\d+(?:\.\d+)?\s*%\s+(?:SENIOR\s+)?NOTES?\s+DUE\s+20\d{2}",
            " ".join(title.upper().split()),
        ):
            continue
        class_signature(title)
        matches.add((title, exchange))
        supported_contexts.add(context_id)
    row_binding: dict[str, Any] = {}
    if (
        len(matches) > 1
        and not ambiguous
        and len({title for title, _ in matches}) == 1
        and context_issuers
        and _compatible_cover_contexts(
            soup, supported_contexts, cutoff, symbol=symbol, allow_exchange_variation=True
        )
    ):
        title = next(iter(matches))[0]
        exchange = None  # The filing does not identify a primary venue.
        row_binding = {
            "registered_class_binding": "same_security_multiple_explicit_listing_contexts",
            "exchanges_at_filing": sorted({venue for _, venue in matches}),
        }
    elif len(matches) != 1 or ambiguous:
        cover_names = {"dei:tradingsymbol", "dei:security12btitle", "dei:securityexchangename"}
        if (
            not any(cover_names & fields.keys() for fields in grouped.values())
            and issuers == {cik}
            and all(values == {cik} for values in context_issuers.values())
        ):
            href = _registered_cover_exhibit(soup)
            if href is not None:
                raise RegistrationCoverExhibit(href, hashlib.sha256(payload).hexdigest())
        title, exchange = _registered_class_row(soup, cik, symbol, context_issuers, cutoff)
        row_binding = {
            "registered_class_binding": "single_security_row_with_compatible_issuer_period_dimensions"
        }
    else:
        title, exchange = next(iter(matches))
    if reported_symbols and reported_symbols != {symbol}:
        row_binding["reported_symbols_at_filing"] = sorted(reported_symbols)
    return {
        **source,
        "symbol_at_cutoff": symbol,
        "security_title": title,
        "exchange_at_filing": exchange,
        "document_sha256": hashlib.sha256(payload).hexdigest(),
        **shared_binding,
        **row_binding,
    }


def _registered_cover_exhibit(soup: BeautifulSoup) -> str | None:
    text = " ".join(soup.get_text(" ", strip=True).split())
    references = set(
        re.findall(
            r"Securities registered pursuant to Section 12\s*\(b\).{0,160}?"
            r"formatted in Inline XBRL\s*:\s*See Exhibit\s+([0-9]{1,3}\.[0-9]{1,2})",
            text,
            re.I,
        )
    )
    if not references:
        return None
    if len(references) != 1:
        raise IdentityEvidenceError("ambiguous_registration_cover_exhibit")
    reference = next(iter(references))
    hrefs: set[str] = set()
    for link in soup.find_all("a", href=True):
        label = re.sub(r"\s+", "", link.get_text(" ", strip=True))
        if re.fullmatch(r"(?:Exhibit)?" + re.escape(reference) + r"[+*\u2020]?", label, re.I):
            href = str(link.get("href"))
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.html?", href) is None:
                raise IdentityEvidenceError("invalid_registration_cover_exhibit_link")
            hrefs.add(href)
    if len(hrefs) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_registration_cover_exhibit_link")
    return next(iter(hrefs))


def _add_shared_registration_contexts(
    soup: BeautifulSoup,
    grouped: dict[str, dict[str, set[str]]],
    primary: bytes,
    href: str,
    cik: str,
    symbol: str,
    cutoff: datetime,
) -> dict[str, Any]:
    """Resolve only exact IXDS context references from the explicitly linked primary."""
    parent = _soup(primary, MAX_RETAINED_DOCUMENT_BYTES)
    parent_issuers: set[str] = set()
    cover_names = {"dei:tradingsymbol", "dei:security12btitle", "dei:securityexchangename"}
    for tag in parent.find_all("ix:nonnumeric"):
        name = str(tag.get("name", "")).lower()
        if name in cover_names:
            raise IdentityEvidenceError("shared_primary_has_conflicting_cover_facts")
        if name == "dei:entitycentralindexkey":
            value = tag.get_text(strip=True)
            if not re.fullmatch(r"[0-9]{1,10}", value):
                raise IdentityEvidenceError("invalid_shared_primary_issuer")
            parent_issuers.add(value.zfill(10))
    if parent_issuers != {cik} or _registered_cover_exhibit(parent) != href:
        raise IdentityEvidenceError("shared_registration_primary_binding_mismatch")
    references = set()
    for context, fields in grouped.items():
        symbols = fields.get("dei:tradingsymbol", set())
        titles = fields.get("dei:security12btitle", set())
        if len(symbols) == 1 and _symbol_matches(
            next(iter(symbols)), symbol, next(iter(titles)) if len(titles) == 1 else None
        ):
            references.add(context)
    if not references or "" in references:
        raise IdentityEvidenceError("missing_shared_registration_context_reference")
    if not (
        _compatible_cover_contexts(parent, references, cutoff, symbol=symbol)
        or _compatible_cover_contexts(
            parent, references, cutoff, symbol=symbol, allow_exchange_variation=True
        )
    ):
        raise IdentityEvidenceError("incompatible_shared_registration_contexts")
    resources: dict[str, str] = {}
    for reference in sorted(references):
        definitions = parent.find_all("xbrli:context", attrs={"id": reference})
        if len(definitions) != 1 or soup.find_all("xbrli:context", attrs={"id": reference}):
            raise IdentityEvidenceError("conflicting_shared_registration_context")
        definition = definitions[0]
        identifiers = definition.find_all("xbrli:identifier")
        if (
            len(identifiers) != 1
            or identifiers[0].get("scheme")
            not in {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
            or identifiers[0].get_text(strip=True).zfill(10) != cik
            or not _compatible_cover_contexts(parent, {reference}, cutoff, symbol=symbol)
        ):
            raise IdentityEvidenceError("invalid_shared_registration_context")
        clone = BeautifulSoup(str(definition), "html.parser").find("xbrli:context")
        if clone is None:
            raise IdentityEvidenceError("missing_shared_registration_context")
        # Keep the parent context's namespace environment; never reinterpret its
        # QName dimensions under a different namespace in the exhibit.
        ancestors = [node for node in definition.parents if isinstance(node, Tag)]
        for ancestor in [*reversed(ancestors), definition]:
            for key, namespace_value in ancestor.attrs.items():
                if key == "xmlns" or key.startswith("xmlns:"):
                    if not isinstance(namespace_value, str):
                        raise IdentityEvidenceError("invalid_shared_context_namespace")
                    clone[key] = namespace_value
        resources[reference] = str(clone)
        soup.append(clone)
    encoded = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    return {
        "shared_registration_contexts": {
            "primary_document_sha256": hashlib.sha256(primary).hexdigest(),
            "relative_href": href,
            "context_ids": sorted(references),
            "context_resources_sha256": hashlib.sha256(encoded).hexdigest(),
            "context_resources_encoding": "sorted_compact_JSON_of_namespace_preserving_context_XML",
            "basis": "exact_context_references_in_same_explicit_Inline_XBRL_document_set",
        }
    }


def _registered_class_row(
    soup: BeautifulSoup,
    cik: str,
    symbol: str,
    context_issuers: dict[str, set[str]],
    cutoff: datetime,
) -> tuple[str, str]:
    """Disambiguate repeated context facts only within one explicit cover row."""
    names = {"dei:tradingsymbol", "dei:security12btitle", "dei:securityexchangename"}
    matches: set[tuple[str, str]] = set()
    for tag in soup.find_all("ix:nonnumeric"):
        if str(tag.get("name", "")).lower() != "dei:tradingsymbol" or not _symbol_matches(
            tag.get_text(" ", strip=True), symbol
        ):
            continue
        row = tag.find_parent("tr")
        if row is None:
            continue
        fields: dict[str, set[str]] = {}
        contexts: set[str] = set()
        for fact in row.find_all("ix:nonnumeric"):
            name = str(fact.get("name", "")).lower()
            if name not in names or fact.find_parent("tr") is not row:
                continue
            contexts.add(str(fact.get("contextref", "")))
            fields.setdefault(name, set()).add(" ".join(fact.get_text(" ", strip=True).split()))
        if (
            set(fields) != names
            or any(len(values) != 1 for values in fields.values())
            or not all(_symbol_matches(value, symbol) for value in fields["dei:tradingsymbol"])
            or not contexts
            or "" in contexts
        ):
            continue
        if context_issuers and any(context_issuers.get(context) != {cik} for context in contexts):
            raise IdentityEvidenceError("symbol_context_issuer_mismatch")
        if len(contexts) > 1 and not _compatible_cover_contexts(
            soup, contexts, cutoff, symbol=symbol
        ):
            continue
        title = next(iter(fields["dei:security12btitle"]))
        exchange = next(iter(fields["dei:securityexchangename"]))
        class_signature(title)
        matches.add((title, exchange))
    if len(matches) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_symbol_class_context")
    return next(iter(matches))


def _compatible_cover_contexts(
    soup: BeautifulSoup,
    references: set[str],
    cutoff: datetime,
    *,
    symbol: str | None = None,
    allow_exchange_variation: bool = False,
) -> bool:
    """Only known security/ticker/exchange axes; shared axes cannot disagree."""
    allowed = {
        "us-gaap:StatementClassOfStockAxis",
        "dei:TradingSymbolAxis",
        "dei:EntityListingsExchangeAxis",
    }
    periods: set[str] = set()
    dimensions: dict[str, set[str]] = {}
    for reference in references:
        definitions = soup.find_all("xbrli:context", attrs={"id": reference})
        if len(definitions) != 1:
            return False
        context = definitions[0]
        period = context.find_all("xbrli:period")
        if len(period) != 1 or context.find_all("xbrldi:typedmember"):
            return False
        dates = period[0].find_all(["xbrli:startdate", "xbrli:enddate", "xbrli:instant"])
        if sorted(node.name for node in dates) not in [
            ["xbrli:instant"],
            ["xbrli:enddate", "xbrli:startdate"],
        ] or len(period[0].find_all(recursive=False)) != len(dates):
            return False
        parsed_dates: dict[str, date] = {}
        for node in dates:
            value = node.get_text(strip=True)
            try:
                parsed = date.fromisoformat(value)
                if parsed > cutoff.date():
                    return False
                parsed_dates[node.name] = parsed
            except ValueError:
                return False
        if (
            "xbrli:startdate" in parsed_dates
            and parsed_dates["xbrli:startdate"] > parsed_dates["xbrli:enddate"]
        ):
            return False
        periods.add(re.sub(r">\s+<", "><", str(period[0])))
        for segment in context.find_all(["xbrli:segment", "xbrli:scenario"]):
            if any(
                child.name != "xbrldi:explicitmember" for child in segment.find_all(recursive=False)
            ):
                return False
        local_axes: set[str] = set()
        for member in context.find_all("xbrldi:explicitmember"):
            axis = str(member.get("dimension", ""))
            value = member.get_text(strip=True)
            if axis not in allowed:
                prefix, _, local = axis.partition(":")
                declarations = {
                    str(tag.get("xmlns:" + prefix))
                    for tag in soup.find_all(True, attrs={"xmlns:" + prefix: True})
                }
                if (
                    local != "TradingSymbolAxis"
                    or symbol is None
                    or value != prefix + ":" + symbol + "Member"
                    or len(declarations) != 1
                    or not re.fullmatch(r"https?://[A-Za-z0-9./_-]+", next(iter(declarations)))
                ):
                    return False
            if axis in local_axes:
                return False
            local_axes.add(axis)
            if not value:
                return False
            dimensions.setdefault(axis, set()).add(value)
        if allow_exchange_variation and "dei:EntityListingsExchangeAxis" not in local_axes:
            return False
    return len(periods) == 1 and all(
        len(values) == 1 or (allow_exchange_variation and axis == "dei:EntityListingsExchangeAxis")
        for axis, values in dimensions.items()
    )


def valid_cusip(value: str) -> bool:
    if re.fullmatch(r"[A-Z0-9]{8}[0-9]", value) is None:
        return False
    total = 0
    for index, char in enumerate(value[:8]):
        number = int(char) if char.isdigit() else ord(char) - 55
        number *= 1 if index % 2 == 0 else 2
        total += number // 10 + number % 10
    return (10 - total % 10) % 10 == int(value[8])


def ownership_subject(payload: bytes, *, cik: str, accession: str) -> dict[str, str]:
    """Authenticate the requested subject within a complete SEC submission.

    A reporting owner's CIK and inclusion in a submissions list are insufficient.
    Only the explicit SUBJECT COMPANY block can supply this leg of the join.
    """
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("empty_or_excessive_document")
    headers = re.findall(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", payload, re.S | re.I)
    if len(headers) != 1 or len(headers[0]) > 256 * 1024:
        raise IdentityEvidenceError("missing_or_ambiguous_SEC_subject_header")
    header = headers[0].replace(b"\r\n", b"\n")
    accessions = re.findall(rb"(?m)^ACCESSION NUMBER:\s*([0-9]{10}-[0-9]{2}-[0-9]{6})\s*$", header)
    if accessions != [accession.encode("ascii")]:
        raise IdentityEvidenceError("SEC_header_accession_mismatch")
    subjects = re.findall(rb"(?ms)^SUBJECT COMPANY:\s*\n(.*?)(?=^\S|\Z)", header)
    if len(subjects) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_SEC_subject_company")
    identifiers = re.findall(rb"CENTRAL INDEX KEY:\s*([0-9]+)", subjects[0])
    if len(identifiers) != 1 or identifiers[0].decode("ascii").zfill(10) != cik:
        raise IdentityEvidenceError("ownership_subject_issuer_mismatch")
    return {
        "subject_cik": cik,
        "accession": accession,
        "SEC_header_sha256": hashlib.sha256(headers[0]).hexdigest(),
        "complete_submission_sha256": hashlib.sha256(payload).hexdigest(),
        "subject_identity_basis": "explicit_SEC_SUBJECT_COMPANY_not_reporting_owner",
    }


def ownership_identity(
    payload: bytes, metadata: dict[str, Any], *, cik: str, cutoff: datetime
) -> dict[str, Any]:
    source = source_metadata(
        metadata, cik=cik, cutoff=cutoff, forms={"SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A"}
    )
    soup = _soup(payload)
    text = " ".join(soup.get_text(" ", strip=True).split())
    titles = re.findall(
        r"\(\s*Name\s+of\s+Issuer\s*\)(.*?)\(\s*Title\s+of\s+Class\s+of\s+Securities\s*\)",
        text,
        re.I,
    )
    candidates: set[str] = set()
    # Some covers use inline colon-labelled fields instead of parenthesized
    # captions. Require the complete local name/class/CUSIP sequence; a class
    # mentioned in Item 2 or another narrative is not a cover field.
    lines = [
        " ".join(line.split())
        for line in soup.get_text("\n", strip=True).splitlines()
        if line.strip()
    ]
    for index, line in enumerate(lines):
        label = re.fullmatch(r"Title\s+of\s+Class\s+of\s+Securities\s*:\s*(.+)", line, re.I)
        if label is None:
            continue
        preceding = lines[max(0, index - 6) : index]
        following = lines[index + 1 : index + 7]
        if not any(
            re.fullmatch(r"Name\s+of\s+Issuer\s*:\s*\S.+", item, re.I) for item in preceding
        ):
            raise IdentityEvidenceError("unbound_ownership_cover_class")
        field = None
        for item in following:
            if re.match(r"(?:Name\s+of\s+Issuer|Title\s+of\s+Class|Item\s+[0-9])\b", item, re.I):
                break
            match = re.fullmatch(r"CUSIP\s+(?:Number|No\.?)\s*:\s*(.+)", item, re.I)
            if match:
                field = match[1]
                break
        if field is None:
            raise IdentityEvidenceError("unbound_ownership_cover_cusip")
        titles.append(label[1])
        value = re.sub(r"[\s-]", "", field).upper()
        if valid_cusip(value):
            candidates.add(value)
    if not titles:
        raise IdentityEvidenceError("missing_ownership_class")
    signatures = {class_signature(title) for title in titles}
    if len(signatures) != 1:
        raise IdentityEvidenceError("ambiguous_ownership_class")
    for label in re.finditer(r"CUSIP\s+(?:Number|No\.?)", text, re.I):
        region = text[max(0, label.start() - 64) : label.end() + 64]
        for match in re.finditer(r"(?<![A-Z0-9])([A-Z0-9]{8}[0-9])(?![A-Z0-9])", region):
            if valid_cusip(match[1]):
                candidates.add(match[1])
    # Some covers separate a CUSIP into groups with whitespace.
    for field in re.findall(
        r"\(\s*Title\s+of\s+Class\s+of\s+Securities\s*\)(.*?)\(\s*CUSIP\s+(?:Number|No\.?)[^)]*\)",
        text,
        re.I,
    ):
        value = re.sub(r"[\s-]", "", field).upper()
        if valid_cusip(value):
            candidates.add(value)
    if len(candidates) != 1:
        raise IdentityEvidenceError("missing_or_ambiguous_cusip")
    return {
        **source,
        "cusip": next(iter(candidates)),
        "security_title": titles[0].strip(),
        "document_sha256": hashlib.sha256(payload).hexdigest(),
    }


def join_identity(periodic: dict[str, Any], ownership: dict[str, Any]) -> dict[str, Any]:
    if periodic["issuer_cik"] != ownership["issuer_cik"]:
        raise IdentityEvidenceError("joined_issuer_mismatch")
    if class_signature(periodic["security_title"]) != class_signature(ownership["security_title"]):
        raise IdentityEvidenceError("joined_share_class_mismatch")
    if not valid_cusip(ownership["cusip"]):
        raise IdentityEvidenceError("invalid_joined_cusip")
    return {
        "issuer_cik": periodic["issuer_cik"],
        "security_id": "cusip:" + ownership["cusip"],
        "symbol_at_cutoff": periodic["symbol_at_cutoff"],
        "security_title": periodic["security_title"],
        "exchange_at_filing": periodic["exchange_at_filing"],
        **(
            {"exchanges_at_filing": periodic["exchanges_at_filing"]}
            if "exchanges_at_filing" in periodic
            else {}
        ),
        **(
            {"reported_symbols_at_filing": periodic["reported_symbols_at_filing"]}
            if "reported_symbols_at_filing" in periodic
            else {}
        ),
        "available_by": max(
            instant(periodic["available_by"]), instant(ownership["available_by"])
        ).isoformat(),
        "periodic_evidence": periodic,
        "ownership_evidence": ownership,
        "identity_basis": "same_SEC_issuer_and_share_class_with_pre_cutoff_reported_CUSIP",
        "current_asset_directory_used_for_selection": False,
    }
