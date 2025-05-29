from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Generator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncSessionTransaction,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.asyncio.engine import AsyncEngine

from jararaca.microservice import AppContext, AppInterceptor

ctx_default_connection_name: ContextVar[str] = ContextVar("ctx_default_connection_name")


def ensure_name(name: str | None) -> str:
    if name is None:
        return ctx_default_connection_name.get()
    return name


@dataclass
class PersistenceCtx:
    session: AsyncSession
    tx: AsyncSessionTransaction


ctx_session_map = ContextVar[dict[str, PersistenceCtx]]("ctx_session_map", default={})


@contextmanager
def providing_session(
    session: AsyncSession,
    tx: AsyncSessionTransaction,
    connection_name: str | None = None,
) -> Generator[None, Any, None]:

    connection_name = ensure_name(connection_name)
    current_map = ctx_session_map.get({})

    token = ctx_session_map.set(
        {**current_map, connection_name: PersistenceCtx(session, tx)}
    )

    try:
        yield
    finally:
        with suppress(ValueError):
            ctx_session_map.reset(token)


@asynccontextmanager
async def providing_new_session(
    connection_name: str | None = None,
) -> AsyncGenerator[AsyncSession, None]:

    current_session = use_session(connection_name)

    async with AsyncSession(
        current_session.bind,
    ) as new_session, new_session.begin() as new_tx:
        with providing_session(new_session, new_tx, connection_name):
            yield new_session


def use_session(connection_name: str | None = None) -> AsyncSession:
    connection_name = ensure_name(connection_name)
    current_map = ctx_session_map.get({})
    if connection_name not in current_map:
        raise ValueError(f"Session not found for connection {connection_name}")

    return current_map[connection_name].session


@contextmanager
def providing_transaction(
    tx: AsyncSessionTransaction,
    connection_name: str | None = None,
) -> Generator[None, Any, None]:
    connection_name = ensure_name(connection_name)

    current_map = ctx_session_map.get({})

    if connection_name not in current_map:
        raise ValueError(f"No session found for connection {connection_name}")

    with providing_session(current_map[connection_name].session, tx, connection_name):
        yield


def use_transaction(connection_name: str | None = None) -> AsyncSessionTransaction:
    current_map = ctx_session_map.get({})
    if connection_name not in current_map:
        raise ValueError(f"Transaction not found for connection {connection_name}")

    return current_map[connection_name].tx


class AIOSQAConfig:
    url: str | AsyncEngine
    connection_name: str

    def __init__(self, url: str | AsyncEngine, connection_name: str = "default"):
        self.url = url
        self.connection_name = connection_name


class AIOSqlAlchemySessionInterceptor(AppInterceptor):

    def __init__(self, config: AIOSQAConfig):
        self.config = config
        self.engine = (
            create_async_engine(self.config.url)
            if isinstance(self.config.url, str)
            else self.config.url
        )

        self.sessionmaker = async_sessionmaker(self.engine)

    @asynccontextmanager
    async def intercept(self, app_context: AppContext) -> AsyncGenerator[None, None]:

        async with self.sessionmaker() as session, session.begin() as tx:
            token = ctx_default_connection_name.set(self.config.connection_name)
            with providing_session(session, tx, self.config.connection_name):
                try:
                    yield
                    if tx.is_active:
                        await tx.commit()
                except Exception as e:
                    await tx.rollback()
                    raise e
                finally:
                    ctx_default_connection_name.reset(token)
