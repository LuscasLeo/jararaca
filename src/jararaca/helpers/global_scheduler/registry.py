# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later


from typing import Any, Awaitable, Callable

GlobalScheduleFnType = Callable[[], Awaitable[Any]]


class GlobalSchedulerRegistry:
    """Registry for the Global Scheduler helper."""

    _REGISTRY: list[GlobalScheduleFnType] = []

    @classmethod
    def register(cls, fn: GlobalScheduleFnType) -> None:
        """Register a scheduled action function.

        Args:
            fn: The scheduled action function to register.
        """
        cls._REGISTRY.append(fn)

    @classmethod
    def get_registered_actions(cls) -> list[GlobalScheduleFnType]:
        """Get the list of registered scheduled action functions.

        Returns:
            A list of registered scheduled action functions.
        """
        return cls._REGISTRY
