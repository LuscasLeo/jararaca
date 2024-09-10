import logging
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Generator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jararaca.microservice import AppContext, AppInterceptor

ctx_session_map = ContextVar[dict[str, AsyncSession]]("ctx_session_map", default={})


@contextmanager
def provide_session(
    connection_name: str, session: AsyncSession
) -> Generator[None, Any, None]:
    current_map = ctx_session_map.get({})

    token = ctx_session_map.set({**current_map, connection_name: session})

    try:
        yield
    finally:
        try:
            ctx_session_map.reset(token)
        except ValueError:
            logging.warning("Session map already reset")


def use_session(connection_name: str = "default") -> AsyncSession:
    current_map = ctx_session_map.get({})
    if connection_name not in current_map:
        raise ValueError(f"Session not found for connection {connection_name}")

    return current_map[connection_name]


@dataclass
class AIOSQAConfig:
    connection_name: str
    url: str


class AIOSqlAlchemySessionInterceptor(AppInterceptor):

    def __init__(self, config: AIOSQAConfig):
        self.config = config
        self.engine = create_async_engine(self.config.url)
        self.sessionmaker = async_sessionmaker(self.engine)

    @asynccontextmanager
    async def intercept(self, app_context: AppContext) -> AsyncGenerator[None, None]:
        async with self.sessionmaker() as session:
            with provide_session(self.config.connection_name, session):
                try:
                    yield
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    raise e
