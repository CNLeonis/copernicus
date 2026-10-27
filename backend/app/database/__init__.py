from functools import lru_cache
from collections.abc import Iterator

from sqlalchemy import Connection, Engine, create_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_connection() -> Iterator[Connection]:
    with get_engine().connect() as connection:
        yield connection
