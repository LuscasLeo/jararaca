# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Make connection URLs safe to print: credentials never reach the logs."""

import re
from urllib.parse import urlsplit, urlunsplit

REDACTED = "***"

# Query parameters whose value is treated as a secret.
_SECRET_QUERY_KEY = re.compile(
    r"pass|pwd|token|secret|credential|api[_-]?key|access[_-]?key|private[_-]?key",
    re.IGNORECASE,
)


def redact_url(url: str) -> str:
    """
    The URL with its password, and the value of query parameters that look like
    secrets, replaced by `***`. Scheme, user, host, port, path (vhost) and the
    other query parameters are kept as written.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return REDACTED

    netloc = parts.netloc
    userinfo, at, hostinfo = netloc.rpartition("@")
    if at:
        user, colon, _password = userinfo.partition(":")
        netloc = f"{user}:{REDACTED}@{hostinfo}" if colon else netloc

    query = "&".join(
        _redact_query_item(item) for item in parts.query.split("&") if item
    )

    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def _redact_query_item(item: str) -> str:
    key, equals, _value = item.partition("=")
    if equals and _SECRET_QUERY_KEY.search(key):
        return f"{key}={REDACTED}"
    return item
