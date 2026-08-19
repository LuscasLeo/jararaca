# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the lazy attribute loader of the top level ``jararaca`` package.
"""

from typing import Any

import pytest

import jararaca

REGISTRY: dict[str, tuple[str, str, str | None]] = jararaca._dynamic_imports


def cold_getattr(name: str) -> Any:
    """Read *name* off the package with every cached export evicted first."""
    package_globals = dict(vars(jararaca))

    for cached in list(REGISTRY):
        package_globals.pop(cached, None)

    return getattr(jararaca, name)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_export_resolves_on_a_cold_first_access(name: str) -> None:
    # Regression: the warm up loop used to walk the whole registry against whichever
    # module had just been imported, so the first access to any export raised
    # `AttributeError: module '<some.module>' has no attribute 'const'`.
    assert cold_getattr(name) is not None


def test_unknown_attributes_still_raise_naming_the_package() -> None:
    with pytest.raises(AttributeError, match="module 'jararaca' has no attribute"):
        jararaca.definitely_not_a_thing


def test_warm_up_caches_siblings_from_the_same_module_only() -> None:
    _, module_name, _ = REGISTRY["Microservice"]
    siblings = {
        name for name, (_, module, _) in REGISTRY.items() if module == module_name
    }
    strangers = set(REGISTRY) - siblings

    cold_getattr("Microservice")
    package_globals = vars(jararaca)

    assert siblings <= set(package_globals)
    assert not (strangers & set(package_globals))


def test_submodule_entries_are_importable() -> None:
    assert cold_getattr("const").__name__ == "jararaca.const"


def test_all_is_available_at_runtime() -> None:
    # Regression: `__all__` used to be defined inside the `if TYPE_CHECKING:` block, so
    # it did not exist at runtime. `dir()` raised and `import *` exported almost nothing.
    assert set(jararaca.__all__) == set(REGISTRY)


def test_dir_lists_every_export() -> None:
    assert set(dir(jararaca)) == set(REGISTRY)


def test_star_import_exports_every_name() -> None:
    namespace: dict[str, Any] = {}

    exec("from jararaca import *", namespace)  # noqa: S102

    assert set(REGISTRY) <= set(namespace)
