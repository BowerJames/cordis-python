# The server contract, kept by both servers, over real sockets: each test runs against each provider.
import asyncio
from typing import Any, AsyncIterator, Iterator

import pytest

from cordis import Component, Context, Inverse
from cordis_loader import Entry, Loader, patched
from cordis_plugins import (
    HTTP,
    ROUTES,
    SERVER_ADDRESS,
    STDERR,
    Matched,
    Request,
    Route,
    RouteTable,
    ServerRequest,
    ServerResponse,
    asyncio_http_server,
    capturing_stdio,
    environment,
    fastapi_http_server,
    httpx_http,
    route_table,
)
from cordis_plugins.testing import provided, run_scenario

pytestmark = pytest.mark.external

async def greet(request: ServerRequest) -> ServerResponse:
    return ServerResponse.text(f"hello {request.params['name']}")

async def broken(request: ServerRequest) -> ServerResponse:
    raise RuntimeError("handler broke")

def routes(added: list[Route]) -> Component[None]:
    def apply(ctx: Context) -> Iterator[Inverse]:
        for route in added:
            yield ctx.get(ROUTES).add(route)
    return Component("routes contributor", apply=apply, requires=(ROUTES,))

@pytest.fixture(params=[asyncio_http_server, fastapi_http_server], ids=["asyncio", "fastapi"])
def server(request: pytest.FixtureRequest) -> Any:
    return request.param

def composition(server: Any, *extra: Entry, max_body: int = 1_048_576) -> list[Entry]:
    capture, _ = capturing_stdio()
    return [Entry("stdio", capture), Entry("env", environment), Entry("routes", route_table),
            Entry("server", server, config={"port": 0, "max_body": max_body}), Entry("http", httpx_http), *extra]

async def test_routes_are_answered_and_the_rest_is_404_or_405(server: Any) -> None:
    async def steps(ctx: Context) -> None:
        base, http = ctx.get(SERVER_ADDRESS), ctx.get(HTTP)
        answered = await http.send(Request("GET", f"{base}/greet/ada"))
        assert (answered.status, answered.text()) == (200, "hello ada")
        assert (await http.send(Request("GET", f"{base}/nowhere"))).status == 404
        refused = await http.send(Request("DELETE", f"{base}/greet/ada"))
        assert (refused.status, refused.header("allow")) == (405, "GET")

    outcome = await run_scenario(composition(server, Entry("app", routes(added=[Route("GET", "/greet/{name}", greet)]))),
                                 steps, requires=(HTTP, SERVER_ADDRESS))
    assert outcome.failures == ()

async def test_a_handler_that_raises_fails_its_own_request_alone_and_is_reported(server: Any) -> None:
    capture, captured = capturing_stdio()

    async def steps(ctx: Context) -> None:
        base, http = ctx.get(SERVER_ADDRESS), ctx.get(HTTP)
        assert (await http.send(Request("GET", f"{base}/broken"))).status == 500
        assert (await http.send(Request("GET", f"{base}/greet/ada"))).status == 200

    entries = patched(composition(server, Entry("app", routes(added=[Route("GET", "/broken", broken),
                                                                              Route("GET", "/greet/{name}", greet)]))),
                      Entry("stdio", capture))
    outcome = await run_scenario(entries, steps, requires=(HTTP, SERVER_ADDRESS, STDERR))
    assert outcome.failures == ()
    assert "GET /broken failed: RuntimeError('handler broke')" in captured.stderr

async def test_a_body_over_the_limit_is_413(server: Any) -> None:
    async def steps(ctx: Context) -> None:
        response = await ctx.get(HTTP).send(Request("POST", f"{ctx.get(SERVER_ADDRESS)}/echo", body=b"x" * 100))
        assert response.status == 413

    async def echo(request: ServerRequest) -> ServerResponse:
        return ServerResponse(200, body=request.body)

    outcome = await run_scenario(composition(server, Entry("app", routes(added=[Route("POST", "/echo", echo)])),
                                             max_body=10), steps, requires=(HTTP, SERVER_ADDRESS))
    assert outcome.failures == ()

async def test_a_route_withdrawn_while_the_server_runs_is_404_from_the_next_request(server: Any) -> None:
    # Routes live in their own table, and a contributor's route is its effect: unload the contributor and the route
    # goes, with the server left running.
    async def steps(ctx: Context) -> None:
        base, http = ctx.get(SERVER_ADDRESS), ctx.get(HTTP)
        assert (await http.send(Request("GET", f"{base}/greet/ada"))).status == 200
        contributor = next(fiber for fiber in ctx.runtime.fibers() if fiber.name == "routes contributor")
        await contributor.dispose()
        assert (await http.send(Request("GET", f"{base}/greet/ada"))).status == 404

    outcome = await run_scenario(composition(server, Entry("app", routes(added=[Route("GET", "/greet/{name}", greet)]))),
                                 steps, requires=(HTTP, SERVER_ADDRESS))
    assert outcome.failures == ()

async def test_swapping_the_server_leaves_every_route_where_it_is() -> None:
    # The route table is its own key, so replacing the server entry reloads the server alone: the contributor is not
    # touched, and the new server answers its route.
    async def steps(ctx: Context) -> None:
        contributor = next(fiber for fiber in ctx.runtime.fibers() if fiber.name == "routes contributor")
        loader = Loader(ctx.runtime.root)
        swapped = [Entry("server", fastapi_http_server, config={"port": 0})]
        await loader.load(swapped)
        await ctx.runtime.settled()
        address = ctx.runtime.root.find(SERVER_ADDRESS)
        assert address is not None
        answered = await ctx.get(HTTP).send(Request("GET", f"{address}/greet/ada"))
        assert (answered.status, answered.text()) == (200, "hello ada")
        assert not contributor.removed
        await loader.load([])

    entries = [Entry("env", environment), Entry("routes", route_table), Entry("http", httpx_http),
               Entry("app", routes(added=[Route("GET", "/greet/{name}", greet)]))]
    outcome = await run_scenario(entries, steps, requires=(HTTP,))
    assert outcome.failures == ()

@pytest.fixture(params=[route_table], ids=lambda keeper: keeper.name)
async def table(request: pytest.FixtureRequest) -> AsyncIterator[RouteTable]:
    # The route table contract, run against every component keeping it, through the contract alone.
    async with provided(ROUTES, request.param) as kept:
        yield kept

async def test_two_routes_answering_the_same_method_and_path_are_refused(table: RouteTable) -> None:
    withdraw = table.add(Route("GET", "/a", greet))
    with pytest.raises(ValueError, match="two routes answer GET /a"):
        table.add(Route("get", "/a", greet))
    with pytest.raises(ValueError, match="must start with /"):
        table.add(Route("GET", "a", greet))
    withdraw()

async def test_withdrawn_routes_free_their_method_and_path(table: RouteTable) -> None:
    withdraw = table.add(Route("GET", "/a", greet))
    withdraw()
    again = table.add(Route("GET", "/a", greet))
    assert [str(route) for route in table.routes()] == ["GET /a"]
    again()

async def test_withdrawing_a_route_takes_it_out_at_once_and_waits_for_the_requests_it_is_answering(
    table: RouteTable,
) -> None:
    # The contributor finishes what it took on while still loaded: withdrawal waits for the route's calls in flight.
    gate = asyncio.Event()

    async def slow(request: ServerRequest) -> ServerResponse:
        await gate.wait()
        return ServerResponse.text("late")

    withdraw = table.add(Route("GET", "/slow", slow))
    found = table.match("GET", "/slow")
    assert isinstance(found, Matched)
    answering = asyncio.ensure_future(found.route.handle(ServerRequest("GET", "/slow")))
    await asyncio.sleep(0)
    draining = withdraw()
    assert table.routes() == () and table.match("GET", "/slow") is None
    assert draining is not None
    waiting = asyncio.ensure_future(draining)
    await asyncio.sleep(0.01)
    assert not waiting.done()
    gate.set()
    assert (await answering).body == b"late"
    await waiting

async def test_withdrawing_a_route_with_nothing_in_flight_is_done_at_once(table: RouteTable) -> None:
    async def quick(request: ServerRequest) -> ServerResponse:
        return ServerResponse.text("quick")

    withdraw = table.add(Route("GET", "/quick", quick))
    assert withdraw() is None
    assert table.routes() == ()
