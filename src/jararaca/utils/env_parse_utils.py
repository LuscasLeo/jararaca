# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from typing import Literal, Optional, TypeVar, overload


def is_env_truffy(var_name: str) -> bool:
    value = os.getenv(var_name, "").lower()
    return value in ("1", "true", "yes", "on")


DF_BOOL_T = TypeVar("DF_BOOL_T", bound="bool")


@overload
def get_env_bool(
    var_name: str, default: DF_BOOL_T
) -> DF_BOOL_T | bool | Literal["invalid"]: ...


@overload
def get_env_bool(
    var_name: str, default: None = None
) -> bool | None | Literal["invalid"]: ...


def get_env_bool(
    var_name: str, default: DF_BOOL_T | None = None
) -> DF_BOOL_T | bool | Literal["invalid"] | None:
    value = os.getenv(var_name)
    if value is None:
        return default
    value_lower = value.lower()
    if value_lower in ("1", "true", "yes", "on"):
        return True
    elif value_lower in ("0", "false", "no", "off"):
        return False
    else:
        return "invalid"


DF_INT_T = TypeVar("DF_INT_T", bound="int | None | Literal[False]")


@overload
def get_env_int(var_name: str, default: None = None) -> int | None | Literal[False]: ...


@overload
def get_env_int(
    var_name: str, default: DF_INT_T
) -> DF_INT_T | int | Literal[False]: ...


def get_env_int(
    var_name: str, default: DF_INT_T = False  # type: ignore[assignment]
) -> DF_INT_T | int | Literal[False]:
    value = os.getenv(var_name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return False


DF_FLOAT_T = TypeVar("DF_FLOAT_T", bound="float | None | Literal[False]")


@overload
def get_env_float(
    var_name: str, default: None = None
) -> float | None | Literal[False]: ...


@overload
def get_env_float(
    var_name: str, default: DF_FLOAT_T
) -> DF_FLOAT_T | float | Literal[False]: ...


def get_env_float(
    var_name: str, default: DF_FLOAT_T = False  # type: ignore[assignment]
) -> DF_FLOAT_T | float | Literal[False]:
    value = os.getenv(var_name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return False


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


def get_env_list(var_name: str, separator: str = ",") -> list[str]:
    value = os.getenv(var_name, "")
    if not value:
        return []
    return [item.strip() for item in value.split(separator) if item.strip()]


def get_env_dict(
    var_name: str, item_separator: str = ",", key_value_separator: str = "="
) -> dict[str, str]:
    value = os.getenv(var_name, "")
    result: dict[str, str] = {}
    if not value:
        return result
    items = [item.strip() for item in value.split(item_separator) if item.strip()]
    for item in items:
        if key_value_separator in item:
            key, val = item.split(key_value_separator, 1)
            result[key.strip()] = val.strip()
    return result
