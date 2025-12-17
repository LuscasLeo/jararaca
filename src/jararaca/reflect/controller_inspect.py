# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Tuple, Type

from frozendict import frozendict

from jararaca.reflect.metadata import SetMetadata, TransactionMetadata


@dataclass(frozen=True)
class ControllerReflect:

    controller_class: Type[Any]
    metadata: Mapping[str, TransactionMetadata]


@dataclass(frozen=True)
class ControllerMemberReflect:
    controller_reflect: ControllerReflect
    member_function: Callable[..., Any]
    metadata: Mapping[str, TransactionMetadata]


def inspect_controller(
    controller: Type[Any],
) -> Tuple[ControllerReflect, Mapping[str, ControllerMemberReflect]]:
    """
    Inspect a controller class to extract its metadata and member functions.

    Args:
        controller (Type[Any]): The controller class to inspect.

    Returns:
        Tuple[ControllerReflect, list[ControllerMemberReflect]]: A tuple containing the controller reflect and a list of member reflects.
    """
    controller_metadata_list = SetMetadata.get(controller)

    controller_metadata_map = frozendict(
        {
            metadata.key: TransactionMetadata(
                value=metadata.value, inherited_from_controller=False
            )
            for metadata in controller_metadata_list
        }
    )

    controller_reflect = ControllerReflect(
        controller_class=controller, metadata=controller_metadata_map
    )

    members = {
        name: ControllerMemberReflect(
            controller_reflect=controller_reflect,
            member_function=member,
            metadata=frozendict(
                {
                    **{
                        key: TransactionMetadata(
                            value=value.value, inherited_from_controller=True
                        )
                        for key, value in controller_metadata_map.items()
                    },
                    **{
                        metadata.key: TransactionMetadata(
                            value=metadata.value, inherited_from_controller=False
                        )
                        for metadata in SetMetadata.get(member)
                    },
                }
            ),
        )
        for name, member in inspect.getmembers_static(
            controller, predicate=inspect.isfunction
        )
    }

    return controller_reflect, members
