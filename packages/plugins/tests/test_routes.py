# Withdrawing a route waits for the calls of its handler in progress. The table is where a server's request meets a
# contributor's handler, and neither declares the other, so the guard that orders unloading along what relies on what
# cannot order a contributor's leaving after the requests it is serving: the route's own inverse does.
import asyncio
from typing import AsyncIterator

import pytest

from cordis import Component, Context, InactiveAccess, Key, Runtime, open_runtime
from cordis_plugins import (
    ROUTES,
    Matched,
    Route,
    RouteTable,
    ServerRequest,
    ServerResponse,
    Socket,
    SocketRoute,
    route_table,
)
from cordis_plugins.testing import provided

ITEMS: Key[dict[str, str]] = Key("items")

@pytest.fixture(params=[route_table], ids=lambda keeper: keeper.__name__)
async def table(request: pytest.FixtureRequest) -> AsyncIterator[RouteTable]:
    async with provided(ROUTES, request.param) as kept:
        yield kept

def call(table: RouteTable, path: str) -> asyncio.Future[ServerResponse]:
    matched = table.match("GET", path)
    assert isinstance(matched, Matched)
    return asyncio.ensure_future(matched.route.handle(ServerRequest("GET", path, params=matched.params)))

async def test_a_route_nobody_is_calling_is_withdrawn_at_once(table: RouteTable) -> None:
    async def greet(request: ServerRequest) -> ServerResponse:
        return ServerResponse.text("hello")

    withdraw = table.add(Route("GET", "/greet", greet))
    assert (await call(table, "/greet")).body == b"hello"
    assert withdraw() is None
    assert table.match("GET", "/greet") is None

async def test_withdrawing_a_route_waits_for_the_calls_in_progress_and_takes_no_new_one(table: RouteTable) -> None:
    release = asyncio.Event()
    log: list[str] = []

    async def slow(request: ServerRequest) -> ServerResponse:
        await release.wait()
        log.append("answered")
        return ServerResponse.text("done")

    withdraw = table.add(Route("GET", "/slow", slow))
    first, second = call(table, "/slow"), call(table, "/slow")
    await asyncio.sleep(0)
    waiting = withdraw()
    assert waiting is not None
    assert table.match("GET", "/slow") is None
    withdrawn = asyncio.ensure_future(waiting)
    await asyncio.sleep(0.01)
    assert not withdrawn.done()
    release.set()
    await withdrawn
    assert log == ["answered", "answered"]
    assert (await first).body == (await second).body == b"done"

async def test_a_socket_route_is_withdrawn_once_its_connections_have_ended(table: RouteTable) -> None:
    closed = asyncio.Event()

    async def session(socket: Socket) -> None:
        await closed.wait()

    async def nothing() -> None:
        return None

    async def unused(*args: object) -> None:
        return None

    withdraw = table.add_socket(SocketRoute("/ws", session))
    route = table.socket("/ws")
    assert route is not None
    connection = asyncio.ensure_future(route.handle(Socket("/ws", (), (), nothing, unused, unused)))
    await asyncio.sleep(0)
    waiting = withdraw()
    assert waiting is not None and table.socket("/ws") is None
    closed.set()
    await waiting
    await connection

async def test_a_call_that_does_not_end_is_waited_for_no_longer_than_the_grace() -> None:
    async def stuck(request: ServerRequest) -> ServerResponse:
        await asyncio.Event().wait()
        raise AssertionError("never answers")

    async with open_runtime(grace=0.01) as runtime:
        runtime.root.use(route_table())
        await runtime.settled()
        table = runtime.root.find(ROUTES)
        assert table is not None
        withdraw = table.add(Route("GET", "/stuck", stuck))
        request = call(table, "/stuck")
        await asyncio.sleep(0)
        waiting = withdraw()
        assert waiting is not None
        await asyncio.wait_for(waiting, 0.5)
        request.cancel()

def store() -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(ITEMS, {"1": "pencil"})
    return Component("store", apply=apply, provides=(ITEMS,))

def api(release: asyncio.Event, log: list[str]) -> Component:
    # Reads what it requires as each request is answered, as a handler does.
    def apply(ctx: Context) -> None:
        async def item(request: ServerRequest) -> ServerResponse:
            await release.wait()
            try:
                return ServerResponse.text(ctx.get(ITEMS)[request.params["id"]])
            except InactiveAccess as error:
                log.append(str(error))
                raise
        ctx.effect(lambda: ctx.get(ROUTES).add(Route("GET", "/items/{id}", item)), "route")
        ctx.effect(lambda: lambda: log.append("api unloaded"), "log")
    return Component("api", apply=apply, requires=(ROUTES, ITEMS))

async def in_flight(runtime: Runtime, release: asyncio.Event, log: list[str]) -> asyncio.Future[ServerResponse]:
    runtime.root.use(route_table())
    runtime.root.use(store())
    runtime.root.use(api(release, log))
    await runtime.settled()
    table = runtime.root.find(ROUTES)
    assert table is not None
    request = call(table, "/items/1")
    await asyncio.sleep(0)
    return request

async def test_a_contributor_unloaded_while_a_request_is_in_flight_still_answers_it(runtime: Runtime) -> None:
    # A reconfiguration, not an ending: unloading is what the framework is for, so it is where this has to hold.
    release = asyncio.Event()
    log: list[str] = []
    request = await in_flight(runtime, release, log)
    contributor = next(fiber for fiber in runtime.fibers() if fiber.name == "api")
    leaving = asyncio.ensure_future(contributor.dispose())
    await asyncio.sleep(0.01)
    assert not leaving.done()
    release.set()
    assert (await request).body == b"pencil"
    await leaving
    assert log == ["api unloaded"]

async def test_a_request_in_flight_as_everything_is_unloaded_is_still_answered() -> None:
    # The same at the end of a run, with nothing arranged for it: the store the handler reads is relied upon, so it
    # outlasts the contributor, which outlasts its request.
    release = asyncio.Event()
    log: list[str] = []
    async with open_runtime() as runtime:
        request = await in_flight(runtime, release, log)
        asyncio.get_running_loop().call_later(0.01, release.set)
    assert (await request).body == b"pencil"
    assert log == ["api unloaded"]
