# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any, Type, TypeVar

from pydantic import BaseModel

from jararaca.core.providers import Token
from jararaca.reflect.decorators import StackableDecorator

DECORATED_CLASS = TypeVar("DECORATED_CLASS", bound=Any)


class RequiresConfig(StackableDecorator):

    def __init__(self, token: Token[Any], config: Type[BaseModel]):
        self.config = config
        self.token = token
