# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""AppLifecycle must release every interceptor that started, on the error path too."""

from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, AsyncGenerator

import pytest

from jararaca.di import Container
from jararaca.lifecycle import AppLifecycle
from jararaca.microservice import AppTransactionContext, Microservice


class RecordingInterceptor:
    """Interceptor with a lifecycle that records when it starts and stops."""

    def __init__(
        self,
        name: str,
        events: list[str],
        fail_on_enter: bool = False,
        fail_on_exit: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.fail_on_enter = fail_on_enter
        self.fail_on_exit = fail_on_exit

    @asynccontextmanager
    async def intercept(
        self, app_context: AppTransactionContext
    ) -> AsyncGenerator[None, None]:
        yield

    def lifecycle(self, app: Microservice, container: Container) -> Any:
        return self._lifecycle()

    @asynccontextmanager
    async def _lifecycle(self) -> AsyncGenerator[None, None]:
        if self.fail_on_enter:
            raise RuntimeError(f"{self.name} failed to start")
        self.events.append(f"enter:{self.name}")
        try:
            yield
        finally:
            self.events.append(f"exit:{self.name}")
            if self.fail_on_exit:
                raise RuntimeError(f"{self.name} failed to stop")


def build_lifecycle(*interceptors: RecordingInterceptor) -> AsyncContextManager[None]:
    app = Microservice(providers=[], controllers=[], interceptors=list(interceptors))
    return AppLifecycle(app, Container(app))()


async def test_finalization_order_is_unchanged() -> None:
    events: list[str] = []
    async with build_lifecycle(
        RecordingInterceptor("a", events), RecordingInterceptor("b", events)
    ):
        events.append("body")

    assert events == ["enter:a", "enter:b", "body", "exit:a", "exit:b"]


async def test_interceptors_finalized_when_body_raises() -> None:
    events: list[str] = []
    with pytest.raises(ValueError, match="boom"):
        async with build_lifecycle(
            RecordingInterceptor("a", events), RecordingInterceptor("b", events)
        ):
            raise ValueError("boom")

    assert events == ["enter:a", "enter:b", "exit:a", "exit:b"]


async def test_entered_interceptors_finalized_when_a_later_enter_fails() -> None:
    events: list[str] = []
    with pytest.raises(RuntimeError, match="b failed to start"):
        async with build_lifecycle(
            RecordingInterceptor("a", events),
            RecordingInterceptor("b", events, fail_on_enter=True),
            RecordingInterceptor("c", events),
        ):
            pytest.fail("the body must not run when startup fails")

    # "b" never entered, so it is not finalized; "c" never started.
    assert events == ["enter:a", "exit:a"]


async def test_one_failing_exit_does_not_skip_the_others() -> None:
    events: list[str] = []
    async with build_lifecycle(
        RecordingInterceptor("a", events, fail_on_exit=True),
        RecordingInterceptor("b", events),
    ):
        pass

    assert events == ["enter:a", "enter:b", "exit:a", "exit:b"]
