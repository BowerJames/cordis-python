# The SQL contract, as every provider must keep it. Each test runs against every provider listed in PROVIDERS, through
# SQL alone, so a new provider joins by being listed. Tables are written in SQL every provider shares: BYTEA is
# Postgres's name for bytes, and SQLite takes any name, keeping bytes as they are.
import asyncio
from pathlib import Path
from typing import Awaitable, Callable

import pytest

from cordis import Context
from cordis_loader import Entry
from cordis_plugins import (
    SQL,
    ConstraintViolation,
    Sql,
    SqlError,
    get_env_os,
    postgres_pool,
    postgres_sql,
    sqlite_database,
    sqlite_sql,
)
from cordis_plugins.testing import run_scenario

type Provider = Callable[[Path], list[Entry]]

PROVIDERS: dict[str, Provider] = {
    "sqlite in memory": lambda directory: [Entry("sqlite", sqlite_database), Entry("sql", sqlite_sql)],
    "sqlite in a file": lambda directory: [Entry("sqlite", sqlite_database, config={"path": directory / "contract.sqlite"}),
                                          Entry("sql", sqlite_sql)],
}

# Postgres needs a server, so its tests are external, and are given longer: the first starts one in Docker.
POSTGRES = pytest.param("postgres", marks=[pytest.mark.external, pytest.mark.timeout(120)])

@pytest.fixture(params=[*PROVIDERS, POSTGRES])
def provider(request: pytest.FixtureRequest, tmp_path: Path) -> list[Entry]:
    if request.param == "postgres":
        return [Entry("env", get_env_os), Entry("postgres", postgres_pool, config={"dsn": request.getfixturevalue("postgres_dsn")}),
                Entry("sql", postgres_sql)]
    return PROVIDERS[request.param](tmp_path)

async def using(provider: list[Entry], steps: Callable[[Sql], Awaitable[None]]) -> None:
    # Runs steps with the provider's Sql once it has loaded; what goes wrong in them is raised as itself.
    async def talking(ctx: Context) -> None:
        await steps(ctx.get(SQL))

    outcome = await run_scenario(provider, talking, requires=(SQL,))
    if outcome.failures:
        raise outcome.failures[0][1]

async def people(sql: Sql) -> None:
    await sql.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, age INTEGER)")

async def names(sql: Sql) -> list[str]:
    return [row["name"] for row in await sql.fetch("SELECT name FROM people ORDER BY id")]


async def test_execute_counts_the_rows_a_write_changed_and_nothing_for_other_statements(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        assert await sql.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, age INTEGER)") == 0
        for id, name in ((1, "ada"), (2, "alan"), (3, "grace")):
            assert await sql.execute("INSERT INTO people (id, name, age) VALUES (:id, :name, 30)", {"id": id, "name": name}) == 1
        assert await sql.execute("UPDATE people SET age = 31 WHERE name LIKE 'a%'") == 2
        assert await sql.execute("UPDATE people SET age = 40 WHERE name = 'nobody'") == 0
        assert await sql.execute("SELECT * FROM people") == 0
        assert await sql.execute("DELETE FROM people") == 3
    await using(provider, steps)

async def test_fetch_gives_every_row_as_a_mapping_of_its_columns_in_order(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        assert await sql.fetch("SELECT * FROM people") == ()
        await sql.execute("INSERT INTO people (id, name, age) VALUES (2, 'grace', 85), (1, 'ada', 36)")
        rows = await sql.fetch("SELECT id, name, age FROM people ORDER BY id")
        assert rows == ({"id": 1, "name": "ada", "age": 36}, {"id": 2, "name": "grace", "age": 85})
        assert [list(row) for row in rows] == [["id", "name", "age"], ["id", "name", "age"]]
        assert await sql.fetch("SELECT name AS who FROM people WHERE age > :age", {"age": 50}) == ({"who": "grace"},)
    await using(provider, steps)

async def test_none_int_float_str_and_bytes_are_read_back_unchanged(provider: list[Entry]) -> None:
    values = {"missing": None, "count": 2**62, "ratio": 0.1, "text": "naïve café ☕", "data": bytes(range(256))}

    async def steps(sql: Sql) -> None:
        await sql.execute("CREATE TABLE kept (missing INTEGER, count BIGINT, ratio DOUBLE PRECISION, text TEXT, data BYTEA)")
        await sql.execute("INSERT INTO kept VALUES (:missing, :count, :ratio, :text, :data)", values)
        [row] = await sql.fetch("SELECT * FROM kept")
        assert dict(row) == values
        assert {name: type(value) for name, value in row.items()} == {name: type(value) for name, value in values.items()}
    await using(provider, steps)

async def test_every_parameter_a_statement_uses_must_be_given(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        with pytest.raises(SqlError):
            await sql.execute("INSERT INTO people (id, name) VALUES (:id, :name)", {"id": 1})
    await using(provider, steps)

async def test_a_statement_the_database_refuses_raises_sql_error_with_the_drivers_own_as_its_cause(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        with pytest.raises(SqlError) as refused:
            await sql.fetch("SELEKT nothing")
        assert type(refused.value) is SqlError
        assert refused.value.__cause__ is not None and not isinstance(refused.value.__cause__, SqlError)
        with pytest.raises(SqlError):
            await sql.fetch("SELECT * FROM no_such_table")
    await using(provider, steps)

async def test_breaking_a_constraint_raises_constraint_violation(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        await sql.execute("INSERT INTO people (id, name) VALUES (1, 'ada')")
        with pytest.raises(ConstraintViolation):
            await sql.execute("INSERT INTO people (id, name) VALUES (2, 'ada')")
        with pytest.raises(ConstraintViolation):
            await sql.execute("INSERT INTO people (id, name) VALUES (1, 'grace')")
        with pytest.raises(ConstraintViolation):
            await sql.execute("INSERT INTO people (id, name) VALUES (3, NULL)")
        assert await names(sql) == ["ada"]
    await using(provider, steps)

async def test_a_transaction_left_normally_commits_all_its_statements(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        async with sql.transaction() as transaction:
            await transaction.execute("INSERT INTO people (id, name) VALUES (1, 'ada')")
            await transaction.execute("INSERT INTO people (id, name) VALUES (2, 'grace')")
            assert await names(transaction) == ["ada", "grace"]
        assert await names(sql) == ["ada", "grace"]
    await using(provider, steps)

async def test_a_transaction_left_by_raising_rolls_back_all_its_statements_and_the_exception_carries_on(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        await sql.execute("INSERT INTO people (id, name) VALUES (1, 'ada')")
        with pytest.raises(ValueError, match="changed my mind"):
            async with sql.transaction() as transaction:
                await transaction.execute("INSERT INTO people (id, name) VALUES (2, 'grace')")
                await transaction.execute("DELETE FROM people WHERE name = 'ada'")
                raise ValueError("changed my mind")
        assert await names(sql) == ["ada"]
    await using(provider, steps)

async def test_a_savepoint_that_fails_rolls_back_only_its_own_statements(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        async with sql.transaction() as transaction:
            await transaction.execute("INSERT INTO people (id, name) VALUES (1, 'ada')")
            with pytest.raises(ConstraintViolation):
                async with transaction.transaction() as savepoint:
                    await savepoint.execute("INSERT INTO people (id, name) VALUES (2, 'grace')")
                    await savepoint.execute("INSERT INTO people (id, name) VALUES (3, 'ada')")
            # The transaction carries on as it was before the savepoint, even where a failed statement would end it.
            await transaction.execute("INSERT INTO people (id, name) VALUES (4, 'alan')")
            async with transaction.transaction() as savepoint:
                await savepoint.execute("INSERT INTO people (id, name) VALUES (5, 'barbara')")
        assert await names(sql) == ["ada", "alan", "barbara"]
    await using(provider, steps)

async def test_statements_outside_a_transaction_never_see_it_partly_done(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        async with sql.transaction() as transaction:
            await transaction.execute("INSERT INTO people (id, name) VALUES (1, 'ada')")
            # Asked while the transaction is half done: it sees none of it, or, waiting, all of it.
            outside = asyncio.ensure_future(sql.fetch("SELECT count(*) AS n FROM people"))
            await asyncio.sleep(0.05)
            await transaction.execute("INSERT INTO people (id, name) VALUES (2, 'grace')")
        [row] = await outside
        assert row["n"] in (0, 2)
    await using(provider, steps)

async def test_a_transaction_cancelled_midway_is_rolled_back(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        inserted = asyncio.Event()

        async def interrupted() -> None:
            async with sql.transaction() as transaction:
                await transaction.execute("INSERT INTO people (id, name) VALUES (1, 'ada')")
                inserted.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(interrupted())
        await inserted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await names(sql) == []
    await using(provider, steps)

async def test_a_transactions_sql_refuses_statements_once_it_has_ended(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        async with sql.transaction() as transaction:
            pass
        with pytest.raises(RuntimeError, match="ended"):
            await transaction.execute("INSERT INTO people (id, name) VALUES (1, 'ada')")
        with pytest.raises(RuntimeError, match="ended"):
            await transaction.fetch("SELECT * FROM people")
    await using(provider, steps)

async def test_the_databases_own_sql_refuses_the_task_holding_one_of_its_transactions(provider: list[Entry]) -> None:
    async def steps(sql: Sql) -> None:
        await people(sql)
        async with sql.transaction() as transaction:
            await transaction.execute("INSERT INTO people (id, name) VALUES (1, 'ada')")
            with pytest.raises(RuntimeError):
                await sql.fetch("SELECT * FROM people")
            with pytest.raises(RuntimeError):
                async with sql.transaction():
                    pass
        assert await names(sql) == ["ada"]
    await using(provider, steps)
