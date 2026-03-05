# SPDX-FileCopyrightText: 2025 Lucas S
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager, suppress
from contextvars import ContextVar
from logging import Handler, LogRecord
from typing import Any, Callable, Generator, Union

LoggerExtraAttributes = Union[str, int, float, bool, None]

LoggerExtraAttributeMap = dict[str, LoggerExtraAttributes]

logger_extra_attrubutes_ctxvar: "ContextVar[LoggerExtraAttributeMap]" = ContextVar(
    "logger_extra_attrubutes_ctxvar", default={}
)


def get_logger_extra_attributes() -> LoggerExtraAttributeMap:
    """Obtém os atributos extras atuais para o logger a partir do contexto.

    Esses atributos podem ser usados para enriquecer os logs com informações adicionais, como IDs de workspace, ações realizadas, etc.

    Retorna:
        LoggerExtraAttributeMap: Um dicionário contendo os atributos extras atuais para o logger.
    """
    return logger_extra_attrubutes_ctxvar.get()


@contextmanager
def providing_logger_extra_attributes(
    **attributes: LoggerExtraAttributes,
) -> Generator[None, Any, None]:
    """Context manager para fornecer atributos extras para o logger.

    Esses atributos podem ser usados para enriquecer os logs com informações adicionais, como IDs de workspace, ações realizadas, etc.

    Exemplo de uso:
        with providing_logger_extra_attributes(workspace_id=str(workspace_id), action="update_configuration"):
            # código que realiza a ação de atualização da configuração
            ...
    Args:
        **attributes: Atributos extras a serem fornecidos para o logger. As chaves devem ser strings e os valores podem ser de tipos primitivos (str, int, float, bool, None).
    """

    old_attributes = logger_extra_attrubutes_ctxvar.get()
    new_attributes = old_attributes | attributes
    token = logger_extra_attrubutes_ctxvar.set(new_attributes)
    try:
        yield
    finally:
        with suppress(ValueError):
            logger_extra_attrubutes_ctxvar.reset(token)


class LoggerExtraInterceptor(Handler):
    """Interceptor de logs para adicionar atributos extras do contexto aos registros de log.

    Este handler deve ser adicionado à configuração do logger para garantir que os atributos extras fornecidos pelo contexto sejam incluídos nos registros de log.
    """

    def __init__(
        self,
        *,
        inject_extra: Callable[[LogRecord], LoggerExtraAttributeMap] | None = None,
        level: int = 0,
    ):
        super().__init__(level)
        self.inject_extra = inject_extra

    def emit(self, record: LogRecord) -> None:
        extra_attributes = get_logger_extra_attributes() | (
            self.inject_extra(record) if self.inject_extra else {}
        )
        for key, value in extra_attributes.items():
            setattr(record, key, value)
