# Shared by every package's tests: keeps the external marker honest, gives each test a runtime of its own, and gives
# a Postgres to the tests that need one.
import asyncio
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from typing import Any, AsyncIterator, NoReturn

import asyncpg
import pytest

from cordis import Runtime, open_runtime
from cordis.testing import Failures


@pytest.fixture(autouse=True)
def internal_tests_stay_off_the_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # Opening a socket is allowed (asyncio needs a socket pair of its own); binding to or connecting to a port is not.
    if request.node.get_closest_marker("external") is not None:
        return

    def refuse(*args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError("internal tests may not use the network; mark the test external")

    for method in ("bind", "connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, method, refuse)


@pytest.fixture
def failures() -> Failures:
    return Failures()

@pytest.fixture
async def runtime(failures: Failures) -> AsyncIterator[Runtime]:
    # A runtime that records failures rather than logging them, closed (every fiber reverted) after the test.
    async with open_runtime(on_error=failures) as runtime:
        yield runtime


# A Postgres the tests may do anything to. Never POSTGRES_DSN, which may name a database that matters.
TEST_POSTGRES_DSN_VARIABLE = "TEST_POSTGRES_DSN"
POSTGRES_IMAGE = "postgres:17-alpine"

def _docker_works() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

async def _answers(dsn: str) -> bool:
    try:
        connection = await asyncpg.connect(dsn, timeout=2)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError):
        return False
    await connection.close()
    return True

@pytest.fixture(scope="session")
def postgres_server() -> Iterator[str]:
    # The one TEST_POSTGRES_DSN names, else one started in Docker for the session and stopped after it, else the
    # tests that need one are skipped.
    given = os.environ.get(TEST_POSTGRES_DSN_VARIABLE)
    if given:
        yield given
        return
    if not _docker_works():
        pytest.skip(f"no Postgres to test against: set {TEST_POSTGRES_DSN_VARIABLE}, or make docker available")
    password = secrets.token_hex(12)
    container = subprocess.run(
        ["docker", "run", "--detach", "--rm", "--env", f"POSTGRES_PASSWORD={password}", "--publish", "127.0.0.1::5432",
         POSTGRES_IMAGE], capture_output=True, text=True, check=True).stdout.strip()
    try:
        deadline = time.monotonic() + 60
        # Docker may not say which port it published until a moment after the container starts.
        while not (published := subprocess.run(["docker", "port", container, "5432/tcp"], capture_output=True,
                                               text=True, check=False).stdout.split()):
            if time.monotonic() > deadline:
                pytest.fail(f"docker did not publish a port for {container[:12]} within 60s")
            time.sleep(0.2)
        dsn = f"postgresql://postgres:{password}@127.0.0.1:{published[0].rsplit(':', 1)[1]}/postgres"
        while not asyncio.run(_answers(dsn)):
            if time.monotonic() > deadline:
                pytest.fail(f"Postgres in {container[:12]} did not answer within 60s")
            time.sleep(0.5)
        yield dsn
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True, check=False)

@pytest.fixture
def postgres_dsn(postgres_server: str) -> Iterator[str]:
    # A schema of its own for each test, found first by every connection through the dsn, and dropped afterwards.
    schema = f"test_{secrets.token_hex(6)}"

    async def run(statement: str) -> None:
        connection = await asyncpg.connect(postgres_server)
        try:
            await connection.execute(statement)
        finally:
            await connection.close()

    asyncio.run(run(f"CREATE SCHEMA {schema}"))
    try:
        # asyncpg passes query parameters it does not know of on to the server as settings.
        separator = "&" if "?" in postgres_server else "?"
        yield f"{postgres_server}{separator}search_path={schema}"
    finally:
        asyncio.run(run(f"DROP SCHEMA {schema} CASCADE"))
