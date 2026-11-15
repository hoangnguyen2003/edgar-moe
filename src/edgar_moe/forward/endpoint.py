"""Strict Postgres endpoint identity; credentials and overrides are never exposed."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict

_QUERY_OPTIONS = {
    "sslmode",
    "sslrootcert",
    "sslcert",
    "sslkey",
    "channel_binding",
    "connect_timeout",
    "application_name",
}


class RestoreIdentityError(ValueError):
    """Fixed redacted code, never a driver message or configured value."""


def endpoint(database_url: str) -> tuple[str, int, str, str]:
    """Canonicalize only documented Neon pooled/direct aliases; reject overrides."""
    try:
        parsed = urlsplit(database_url)
        options = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        keys = [key for key, _ in options]
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or parsed.fragment
            or any(key not in _QUERY_OPTIONS for key in keys)
            or len(keys) != len(set(keys))
            or any(character in database_url for character in "\r\n\0")
        ):
            raise ValueError
        parsed_fields = conninfo_to_dict(database_url)
        if any(not isinstance(value, str) for value in parsed_fields.values()):
            raise ValueError
        fields = {key: str(value) for key, value in parsed_fields.items()}
        host = fields.get("host", "").lower()
        port = int(fields.get("port", "5432"))
        database, role = fields.get("dbname", ""), fields.get("user", "")
        if (
            not host
            or not database
            or not role
            or not fields.get("password")
            or host.startswith("/")
            or "," in host
            or any(character.isspace() for character in host)
            or any(character in database + role for character in "\r\n\0")
            or not 1 <= port <= 65535
            or fields.get("sslmode") not in {"require", "verify-ca", "verify-full"}
        ):
            raise ValueError
        # No broad alias/DNS/IP collapsing. Other providers must use the exact
        # same configured hostname for owner and auditor.
        first, separator, rest = host.partition(".")
        if host.endswith(".neon.tech") and first.startswith("ep-") and first.endswith("-pooler"):
            host = first.removesuffix("-pooler") + separator + rest
        return host, port, database, role
    except (TypeError, ValueError, psycopg.Error):
        raise RestoreIdentityError("invalid_database_url") from None
