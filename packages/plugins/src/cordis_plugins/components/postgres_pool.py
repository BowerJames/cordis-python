import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

import asyncpg

from cordis import Component, Context, Inverse

from ..contracts import GET_ENV, POSTGRES, POSTGRES_DSN_VARIABLE, PostgresPool


@dataclass(frozen=True, slots=True)
class PostgresPoolConfig:
    dsn: str | None = None
    min_size: int = 1
    max_size: int = 10

async def _postgres_pool(ctx: Context, config: PostgresPoolConfig) -> AsyncIterator[Inverse]:
    chosen = config.dsn or ctx.get(GET_ENV)(POSTGRES_DSN_VARIABLE)
    if not chosen:
        raise LookupError(f"postgres_pool was given no dsn, and {POSTGRES_DSN_VARIABLE} is not set")
    pool: PostgresPool = await asyncpg.create_pool(chosen, min_size=config.min_size, max_size=config.max_size)

    async def close() -> None:
        try:
            await pool.close()
        except asyncio.CancelledError:
            # Out of time to close politely: every connection is dropped at once.
            pool.terminate()
            raise

    yield close
    yield ctx.provide(POSTGRES, pool)

# Owns a pool of connections to the database at dsn, else POSTGRES_DSN: connected as the component loads, so a database
# that cannot be reached fails it, and closed as it unloads, once every connection taken is back.
postgres_pool = Component("postgres pool", _postgres_pool, config=PostgresPoolConfig, requires=(GET_ENV,),
                          provides=(POSTGRES,))
