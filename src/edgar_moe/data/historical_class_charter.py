"""A bounded incorporated two-class charter and share-count-only amendment.

Only a missing nonvoting qualifier can be supplied. The ownership cover must
already identify Class B, the same SEC subject, and one CUSIP. Neither a current
directory nor a third-party holdings table supplies issuer identity.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from edgar_moe.data.historical_governance import _par_value
from edgar_moe.data.historical_identity import (
    MAX_DOCUMENT_BYTES,
    MAX_RETAINED_DOCUMENT_BYTES,
    IdentityEvidenceError,
    class_signature,
    instant,
)


def _name(value: str) -> str:
    # Only the terminal legal suffix is expanded; issuer names are not aliased.
    value = re.sub(r"\bCORPORATION\s*\.?$", "CORP", value.strip(), flags=re.I)
    return "".join(char for char in value.casefold() if char.isalnum())


def _date(value: str) -> str:
    try:
        return datetime.strptime(value, "%B %d, %Y").date().isoformat()
    except ValueError as error:
        raise IdentityEvidenceError("invalid_class_charter_reference_date") from error


def class_charter_references(payload: bytes) -> list[dict[str, str]] | None:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_RETAINED_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_class_charter_index_bytes")
    rows = []
    for row in BeautifulSoup(payload, "html.parser").find_all("tr"):
        if row.find("tr") is None:
            text = " ".join(row.get_text(" ", strip=True).split())
            if re.search(r"(?:certificate|articles) of (?:incorporation|amendment)", text, re.I):
                rows.append((row, text))
    if len(rows) != 2:
        return None
    dates = r"[A-Za-z]+ [0-9]{1,2}, [0-9]{4}"
    restated = re.fullmatch(
        r"3\.1 Restated Certificate of Incorporation of registrant, incorporated into this "
        r"report by reference to Exhibit 3 \( i\) of (.+?)[’']s Form 10-Q for the quarter "
        rf"ended ({dates}), filed on ({dates}) \(File No\. ([0-9-]+)\)\.",
        rows[0][1],
    )
    amended = re.fullmatch(
        r"3\.2 Certificate of Amendment of Restated Certificate of Incorporation of "
        r"registrant, incorporated into this report by reference to Exhibit 3\.1 of "
        rf"(.+?)[’']s Form 8-K filed on ({dates}) \(File No\. ([0-9-]+)\)\.",
        rows[1][1],
    )
    if restated is None or amended is None:
        return None
    if _name(restated[1]) != _name(amended[1]) or not _name(restated[1]):
        raise IdentityEvidenceError("class_charter_reference_issuer_conflict")
    base_date, amendment_date = _date(restated[3]), _date(amended[2])
    if not _date(restated[2]) <= base_date < amendment_date:
        raise IdentityEvidenceError("class_charter_reference_date_order")
    references = []
    for (row, text), form, filed, exhibit in zip(
        rows, ("10-Q", "8-K"), (base_date, amendment_date), ("3.1", "3.1"), strict=True
    ):
        # Formatting can split one hyperlink into several adjacent spans.
        # Every href must still be identical and the complete row is retained.
        links = {str(a["href"]) for a in row.find_all("a", href=True)}
        if len(links) != 1:
            raise IdentityEvidenceError("ambiguous_class_charter_index_links")
        references.append(
            {
                "href": next(iter(links)),
                "referenced_form": form,
                "referenced_filing_date": filed,
                "referenced_exhibit": exhibit,
                "reported_issuer_name": restated[1],
                "index_description_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    return references


def _source(
    payload: bytes, reference: dict[str, str], cik: str, cutoff: datetime
) -> tuple[str, dict[str, str]]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_DOCUMENT_BYTES:
        raise IdentityEvidenceError("invalid_class_charter_source_bytes")
    if re.fullmatch(r"[0-9]{10}", cik) is None or int(cik) == 0:
        raise IdentityEvidenceError("invalid_class_charter_issuer")
    if (
        re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", reference["accession"]) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.html?", reference["filename"]) is None
    ):
        raise IdentityEvidenceError("invalid_class_charter_source_identity")
    headers = re.findall(rb"<SEC-HEADER>[^\n]*\n(.*?)</SEC-HEADER>", payload, re.S | re.I)
    if len(headers) != 1 or len(headers[0]) > 256 * 1024:
        raise IdentityEvidenceError("ambiguous_class_charter_header")
    header = headers[0].replace(b"\r\n", b"\n")
    for field, expected in [
        ("ACCESSION NUMBER", reference["accession"]),
        ("CONFORMED SUBMISSION TYPE", reference["referenced_form"]),
    ]:
        values = re.findall(rb"(?m)^" + field.encode() + rb":[ \t]*([^\n]+)", header)
        if values != [expected.encode()]:
            raise IdentityEvidenceError("class_charter_header_identity_mismatch")
    filers = re.findall(rb"(?ms)^FILER:\s*\n(.*?)(?=^\S|\Z)", header)
    if len(filers) != 1:
        raise IdentityEvidenceError("ambiguous_class_charter_filer")
    ids = re.findall(rb"CENTRAL INDEX KEY:[ \t]*([0-9]+)", filers[0])
    names = re.findall(rb"COMPANY CONFORMED NAME:[ \t]*([^\n]+)", filers[0])
    try:
        name = names[0].decode("utf-8") if len(names) == 1 else ""
        filed = re.findall(rb"(?m)^FILED AS OF DATE:[ \t]*([0-9]{8})[ \t]*$", header)
        accepted = re.findall(rb"<ACCEPTANCE-DATETIME>([0-9]{14})", header)
        if len(filed) != 1 or len(accepted) != 1:
            raise ValueError("dates")
        filing_date = datetime.strptime(filed[0].decode(), "%Y%m%d").date()
        acceptance_date = datetime.strptime(accepted[0].decode(), "%Y%m%d%H%M%S").date()
    except (UnicodeDecodeError, ValueError) as error:
        raise IdentityEvidenceError("invalid_class_charter_header_dates_or_name") from error
    available = datetime.combine(filing_date + timedelta(days=14), time.min, UTC)
    if (
        len(ids) != 1
        or ids[0].decode().zfill(10) != cik
        or not _name(name)
        or _name(name) != _name(reference["reported_issuer_name"])
        or filing_date.isoformat() != reference["referenced_filing_date"]
        or acceptance_date > filing_date
        or cutoff.utcoffset() is None
        or available > cutoff
    ):
        raise IdentityEvidenceError("unbound_class_charter_issuer_or_date")
    documents = re.findall(rb"<DOCUMENT>(.*?)</DOCUMENT>", payload, re.S | re.I)
    if not 1 <= len(documents) <= 128:
        raise IdentityEvidenceError("invalid_class_charter_document_set")
    matching = [
        doc
        for doc in documents
        if re.findall(rb"(?im)^<FILENAME>[ \t]*([^\r\n]+)", doc) == [reference["filename"].encode()]
    ]
    if len(matching) != 1:
        raise IdentityEvidenceError("ambiguous_class_charter_exhibit")
    doc = matching[0]
    if re.findall(rb"(?im)^<TYPE>[ \t]*([^\r\n]+)", doc) != [b"EX-3.1"]:
        raise IdentityEvidenceError("class_charter_exhibit_type_mismatch")
    texts = re.findall(rb"<TEXT>(.*?)</TEXT>", doc, re.S | re.I)
    if len(texts) != 1:
        raise IdentityEvidenceError("ambiguous_class_charter_exhibit_text")
    text = " ".join(BeautifulSoup(texts[0], "html.parser").get_text(" ", strip=True).split())
    return text, {
        **reference,
        "reported_local_acceptance_datetime": accepted[0].decode(),
        "available_by": available.isoformat(),
        "availability_basis": "SEC_header_filing_date_plus_fourteen_calendar_days_date_only",
        "complete_submission_sha256": hashlib.sha256(payload).hexdigest(),
        "SEC_header_sha256": hashlib.sha256(headers[0]).hexdigest(),
        "document_sha256": hashlib.sha256(texts[0]).hexdigest(),
    }


def _authorized(text: str, *, amended: bool) -> tuple[int, int, int]:
    par = (
        r"par value of Fifteen Cents \(\$0\.15\) per share"
        if amended
        else (r"the par value of Fifteen Cents \(15¢\) each")
    )
    # Only this observed two-class authorization template is supported. Words
    # outside it cannot establish a different class or supply voting rights.
    match = re.fullmatch(
        r"The total number of shares of all classes of stock which the Corporation shall "
        r"have authority to issue is [A-Za-z -]+ \(([0-9,]+)\) shares, divided into "
        rf"\(a\) [A-Za-z -]+ \(([0-9,]+)\) shares of Class A Common Stock of {par}; "
        rf"and \(b\) [A-Za-z -]+ \(([0-9,]+)\) shares of Class B Common Stock of {par}\.",
        text,
    )
    if match is None:
        raise IdentityEvidenceError("unsupported_class_charter_authorization")
    counts = tuple(int(v.replace(",", "")) for v in match.groups())
    if any(n <= 0 for n in counts) or counts[0] != counts[1] + counts[2]:
        raise IdentityEvidenceError("class_charter_share_count_conflict")
    return counts[0], counts[1], counts[2]


def class_charter_voting_evidence(
    sources: list[bytes],
    references: list[dict[str, str]],
    *,
    cik: str,
    expected_title: str,
    cutoff: datetime,
) -> dict[str, Any]:
    if len(sources) != 2 or len(references) != 2:
        raise IdentityEvidenceError("incomplete_class_charter_chain")
    if class_signature(expected_title) != ("common", "B", "nonvoting") or _par_value(
        expected_title
    ) != Decimal("0.15"):
        raise IdentityEvidenceError("unsupported_class_charter_registered_class")
    if [r["referenced_form"] for r in references] != ["10-Q", "8-K"]:
        raise IdentityEvidenceError("class_charter_chain_order")
    (base, base_proof), (amendment, amendment_proof) = [
        _source(payload, reference, cik, cutoff)
        for payload, reference in zip(sources, references, strict=True)
    ]
    name = references[0]["reported_issuer_name"]
    if (
        _name(name) != _name(references[1]["reported_issuer_name"])
        or base_proof["available_by"] >= amendment_proof["available_by"]
    ):
        raise IdentityEvidenceError("class_charter_chain_issuer_or_date_conflict")
    legal = re.findall(r"FIRST\s*: The name of this Corporation is (.+?)\. SECOND\s*:", base)
    articles = re.findall(r"FOURTH\s*: (.*?) FIFTH\s*:", base)
    if (
        len(legal) != 1
        or _name(legal[0]) != _name(name)
        or len(articles) != 1
        or len(re.findall(r"\bFOURTH\s*:", base)) != 1
        or re.search(r"\bClass [A-Z0-9]+\b", base.replace(articles[0], ""))
        or re.search(
            r"\bcommon (?:stock|shares)\b|\bpreferred\b", base.replace(articles[0], ""), re.I
        )
    ):
        raise IdentityEvidenceError("ambiguous_class_charter_authorized_article")
    sections = re.fullmatch(
        r"(.+?\.) Authorized but unissued shares of Class A Common Stock and of Class B "
        r"Common Stock may be issued and sold from time to time by the Corporation for "
        r"such consideration and upon such terms as may from time to time be fixed by "
        r"the Board of Directors, without action by the stockholders\. Rights of Class A "
        r"Common Stock and Class B Common Stock \. (.*?) General Provisions \. (.+)",
        articles[0],
    )
    rights = (
        "Every share of the common stock of both classes, whenever and for whatever "
        "consideration issued, shall be entitled to the same rights as every other share "
        "of common stock in all distributions of earnings or assets of the Corporation "
        "distributable to the holders of the common stock. Except as herein provided, "
        "the holders of the Class A Common Stock shall have full and exclusive voting "
        "powers. The Class B Common Stock shall be in all respects equal and identical "
        "to the Class A Common Stock except that the holders of the Class B Common "
        "Stock shall have no voting powers in the election of directors, or on any "
        "question, except as otherwise provided by the laws of Delaware."
    )
    if (
        sections is None
        or sections[2] != rights
        or re.search(r"\bvot(?:e|es|ing)\b|\bpreferred\b", sections[3], re.I)
        or re.search(r"\b[Cc]lass [A-Z0-9]{1,8}\b", sections[3])
    ):
        raise IdentityEvidenceError("unsupported_class_charter_rights")
    original_counts = _authorized(sections[1], amended=False)
    prefix = (
        "The Restated Certificate of Incorporation of the Corporation is hereby amended "
        "by deleting the first paragraph of Article FOURTH thereof and inserting the "
        "following in lieu thereof: "
    )
    replacements = re.findall(re.escape(prefix) + r"[“\"]FOURTH: (.*?)[”\"]", amendment)
    if (
        len(replacements) != 1
        or len(re.findall(r"\bhereby amended\b", amendment)) != 1
        or len(re.findall(r"\bArticle\s+[A-Z]+\b", amendment)) != 1
        or re.search(
            r"\bvot(?:e|es|ing)\b|\bClass [A-Z0-9]+\b", amendment.replace(replacements[0], "")
        )
        or _name(name) not in _name(amendment)
    ):
        raise IdentityEvidenceError("unsupported_class_charter_amendment_scope")
    # Match the whole amendment, not just an authorization excerpt. A second
    # operative clause, another issuer, or additional rights language cannot be
    # discarded as surrounding prose. Signature text is not operative evidence.
    issuer = re.escape(name)
    skeleton = re.fullmatch(
        rf"Exhibit CERTIFICATE OF AMENDMENT TO THE RESTATED CERTIFICATE OF INCORPORATION OF {issuer} "
        r"Pursuant to Section 242 of the General Corporation Law of the State of Delaware "
        rf"The undersigned authorized officer of {issuer}, a corporation duly organized and "
        r"existing under the General Corporation Law of the State of Delaware \(the [“\"]Corporation[”\"]\), "
        r"hereby certifies that an amendment to the Corporation[’']s Restated Certificate of "
        r"Incorporation has been duly adopted in accordance with the provisions of Section 242 "
        r"of the General Corporation Law of the State of Delaware, as follows: "
        + re.escape(prefix)
        + r"[“\"]FOURTH: "
        + re.escape(replacements[0])
        + r"[”\"] The amendment set forth above was adopted in accordance with Section 242 of "
        r"the General Corporation Law of the State of Delaware\. This Certificate of Amendment "
        r"shall not become effective until 4:00 p\.m\. Eastern Daylight Time on the date on which "
        r"it is filed with the Delaware Secretary of State\. \[Signature Page Follows\] "
        r"IN WITNESS WHEREOF, the Corporation has caused this Certificate of Amendment to be "
        r"executed by its duly authorized officer on this ([0-9]{1,2})(?:st|nd|rd|th) day of "
        rf"([A-Za-z]+, [0-9]{{4}})\. {issuer} By: /s/ ([A-Za-z., -]+?) "
        r"Title: [A-Za-z, -]+ Name: \3",
        amendment,
        re.I,
    )
    if skeleton is None:
        raise IdentityEvidenceError("unrecognized_class_charter_amendment_text")
    executed = _date(skeleton[2].replace(",", " " + skeleton[1] + ","))
    if (
        not references[0]["referenced_filing_date"]
        < executed
        <= references[1]["referenced_filing_date"]
    ):
        raise IdentityEvidenceError("class_charter_amendment_execution_date_conflict")
    updated_counts = _authorized(replacements[0], amended=True)
    return {
        "issuer_cik": cik,
        "security_title": expected_title,
        "reported_par_value": "$0.15",
        "available_by": amendment_proof["available_by"],
        "sources": [base_proof, amendment_proof],
        "original_authorized_share_counts": list(original_counts),
        "amended_authorized_share_counts": list(updated_counts),
        "authorized_article_sha256": hashlib.sha256(articles[0].encode()).hexdigest(),
        "rights_text_sha256": hashlib.sha256(rights.encode()).hexdigest(),
        "amendment_scope": "replace_first_authorized_share_count_paragraph_only",
        "voting_qualifier": "nonvoting",
        "amendment_executed_date": executed,
        "statutory_voting_exceptions_preserved": True,
    }


def qualify_class_charter_ownership(
    periodic: dict[str, Any], ownership: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    if (
        class_signature(periodic["security_title"]) != ("common", "B", "nonvoting")
        or class_signature(ownership["security_title"]) != ("common", "B", "")
        or class_signature(evidence["security_title"]) != ("common", "B", "nonvoting")
        or len({periodic["issuer_cik"], ownership["issuer_cik"], evidence["issuer_cik"]}) != 1
        or instant(evidence["available_by"]) > instant(ownership["accepted_at"])
        or evidence["voting_qualifier"] != "nonvoting"
        or _par_value(periodic["security_title"]) != _par_value(evidence["reported_par_value"])
        or (
            "$" in ownership["security_title"]
            and _par_value(ownership["security_title"])
            != _par_value(evidence["reported_par_value"])
        )
    ):
        raise IdentityEvidenceError("unbound_class_charter_ownership_qualifier")
    return {
        **ownership,
        "security_title": "Non-voting " + ownership["security_title"],
        "reported_security_title": ownership["security_title"],
        "class_qualifier_basis": "explicit_incorporated_two_class_charter_and_count_only_amendment",
    }
