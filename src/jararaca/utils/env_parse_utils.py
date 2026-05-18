# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from typing import Optional, TypeVar, overload

AFFIRMATIVE_STRINGS = {"1", "true", "yes", "on"}
NEGATIVE_STRINGS = {"0", "false", "no", "off"}


DF_BOOL_T = TypeVar("DF_BOOL_T", bound="bool")


@overload
def get_env_bool(var_name: str, default: DF_BOOL_T) -> DF_BOOL_T | bool: ...


@overload
def get_env_bool(var_name: str, default: None = None) -> bool | None: ...


def get_env_bool(
    var_name: str, default: DF_BOOL_T | None = None
) -> DF_BOOL_T | bool | None:
    value = os.getenv(var_name)
    if value is None:
        return default
    value_lower = value.lower()
    if value_lower in AFFIRMATIVE_STRINGS:
        return True
    elif value_lower in NEGATIVE_STRINGS:
        return False
    else:
        raise ValueError(
            f"Environment variable '{var_name}' has an invalid boolean value: '{value}'. Try one of {AFFIRMATIVE_STRINGS.union(NEGATIVE_STRINGS)} (case-insensitive)."
        )


def get_abs_env_bool(
    var_name: str, default: DF_BOOL_T | None = None
) -> DF_BOOL_T | bool:
    result = get_env_bool(var_name, default)
    if result is None:
        if default is None:
            raise ValueError(
                f"Environment variable '{var_name}' is not set and no default value provided"
            )
        return default
    return result


DF_INT_T = TypeVar("DF_INT_T", bound="int | None")


@overload
def get_env_int(var_name: str, default: None = None) -> int | None: ...


@overload
def get_env_int(var_name: str, default: DF_INT_T) -> DF_INT_T | int: ...


def get_env_int(
    var_name: str, default: DF_INT_T = None  # type: ignore[assignment]
) -> DF_INT_T | int:
    value = os.getenv(var_name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"Environment variable '{var_name}' has an invalid integer value: '{value}'."
        )


DF_STR_T = TypeVar("DF_STR_T", bound="Optional[str]")


@overload
def get_env_str(var_name: str, default: None = None) -> str | None: ...


@overload
def get_env_str(var_name: str, default: DF_STR_T) -> DF_STR_T | str: ...


def get_env_str(var_name: str, default: DF_STR_T = None) -> DF_STR_T | str:  # type: ignore[assignment]
    value = os.getenv(var_name)
    if value is None:
        return default
    return value
