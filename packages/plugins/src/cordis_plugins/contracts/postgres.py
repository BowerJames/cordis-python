import asyncpg

from cordis import Key

# Postgres itself, for whatever needs more than the SQL contract, such as LISTEN/NOTIFY or COPY: an asyncpg pool.
# A connection taken from it is given back by leaving pool.acquire(); the pool closes once every connection is back.
type PostgresPool = asyncpg.Pool[asyncpg.Record]

POSTGRES: Key[PostgresPool] = Key("postgres")
# Where to connect when the composition does not say: a libpq connection string or URI, such as
# postgresql://user:password@localhost:5432/database.
POSTGRES_DSN_VARIABLE = "POSTGRES_DSN"
