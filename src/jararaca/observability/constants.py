# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

TRACEPARENT_KEY = "traceparent"
"""ASGI scope key holding the full W3C ``traceparent`` value of the root span."""

TRACE_ID_KEY = "jararaca.trace_id"
"""ASGI scope key holding the bare 32 hex char trace id of the root span."""

__all__ = ["TRACEPARENT_KEY", "TRACE_ID_KEY"]
