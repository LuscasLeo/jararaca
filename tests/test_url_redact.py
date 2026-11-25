# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from jararaca.utils.url_redact import redact_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "amqp://user:s3cr3t@rabbit:5672/vhost?exchange=main&prefetch_count=60",
            "amqp://user:***@rabbit:5672/vhost?exchange=main&prefetch_count=60",
        ),
        (
            "amqps://user:p%40ss%3Aword@rabbit/prod?exchange=main",
            "amqps://user:***@rabbit/prod?exchange=main",
        ),
        ("amqp://user:p@ss@rabbit/", "amqp://user:***@rabbit/"),
        ("amqp://user@rabbit/vhost", "amqp://user@rabbit/vhost"),
        ("amqp://rabbit/vhost?exchange=x", "amqp://rabbit/vhost?exchange=x"),
        ("amqp://:onlypassword@rabbit/", "amqp://:***@rabbit/"),
        (
            "redis://localhost:6379/0?password=hunter2&db=1&api_key=abc",
            "redis://localhost:6379/0?password=***&db=1&api_key=***",
        ),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert redact_url(url) == expected


def test_unparseable_url_is_fully_redacted() -> None:
    assert redact_url("amqp://[::1") == "***"
