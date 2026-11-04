# Shared by every package's tests: keeps the external marker honest, and gives each test a runtime of its own.
import socket
from typing import Any, AsyncIterator, NoReturn

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
