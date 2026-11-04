import asyncio
from typing import Any

import pytest

from cordis import Component, Context
from cordis_loader import Entry
from plugins import (
    HTTP,
    ROUTES,
    SERVER_ADDRESS,
    HTTPConnectionError,
    HTTPError,
    HTTPTimeout,
    Request,
    Response,
    Route,
    ServerRequest,
    ServerResponse,
    asyncio_http_server,
    environment,
    http_requests,
    httpx_http,
    recording_http,
    respond,
    route_table,
)
from plugins.testing import run_scenario


async def test_every_exchange_is_shown_to_each_observer_and_observers_withdraw_independently() -> None:
    fake, sent = recording_http(respond(204, "No Content"))
    seen: list[str] = []

    async def steps(ctx: Context) -> None:
        http = ctx.get(HTTP)
        first = http.observe(lambda request, outcome: seen.append(f"first {request.url}"))
        http.observe(lambda request, outcome: seen.append(f"second {request.url}"))
        await http.send(Request("GET", "https://a.example"))
        first()
        await http.send(Request("GET", "https://b.example"))

    outcome = await run_scenario([Entry("http", fake)], steps, requires=(HTTP,))
    assert outcome.status == 0
    assert seen == ["first https://a.example", "second https://a.example", "second https://b.example"]
    assert [request.url for request in sent] == ["https://a.example", "https://b.example"]

async def test_a_failed_exchange_is_shown_to_observers_and_raised() -> None:
    fake, _ = recording_http(HTTPConnectionError("refused"))
    seen: list[Any] = []

    async def steps(ctx: Context) -> None:
        ctx.get(HTTP).observe(lambda request, outcome: seen.append(outcome))
        with pytest.raises(HTTPConnectionError):
            await ctx.get(HTTP).send(Request("GET", "https://a.example"))

    assert (await run_scenario([Entry("http", fake)], steps, requires=(HTTP,))).status == 0
    assert [type(error) for error in seen] == [HTTPConnectionError]

def test_a_response_decodes_with_the_charset_it_declares() -> None:
    response = Response(200, "OK", (("Content-Type", "text/plain; charset=latin-1"),), "café".encode("latin-1"),
                        Request("GET", "x"))
    assert response.text() == "café"
    assert response.header("content-type") == "text/plain; charset=latin-1"

def slow_route() -> Component:
    async def slow(request: ServerRequest) -> ServerResponse:
        await asyncio.sleep(1)
        return ServerResponse.text("late")

    async def hello(request: ServerRequest) -> ServerResponse:
        return ServerResponse(201, (("X-Seen", request.header("X-Sent") or ""),), request.body)

    def apply(ctx: Context) -> None:
        ctx.effect(lambda: ctx.get(ROUTES).add(Route("GET", "/slow", slow)))
        ctx.effect(lambda: ctx.get(ROUTES).add(Route("POST", "/hello", hello)))
    return Component("routes", apply=apply, requires=(ROUTES,))

@pytest.fixture(params=[httpx_http, http_requests], ids=["httpx", "requests"])
def client(request: pytest.FixtureRequest) -> Any:
    return request.param

@pytest.mark.external
async def test_a_client_sends_headers_and_body_and_reads_the_whole_response(client: Any) -> None:
    async def steps(ctx: Context) -> None:
        response = await ctx.get(HTTP).send(Request("POST", f"{ctx.get(SERVER_ADDRESS)}/hello",
                                                    (("X-Sent", "yes"),), b"body"))
        assert (response.status, response.header("x-seen"), response.body) == (201, "yes", b"body")
        assert ("X-Sent", "yes") in response.sent.headers or ("x-sent", "yes") in response.sent.headers

    outcome = await run_scenario([
        Entry("env", lambda: environment({})), Entry("routes", route_table),
        Entry("server", asyncio_http_server, config={"port": 0}), Entry("http", client), Entry("app", slow_route),
    ], steps, requires=(HTTP, SERVER_ADDRESS))
    assert outcome.failures == ()

@pytest.mark.external
async def test_a_request_that_takes_longer_than_its_timeout_raises_http_timeout(client: Any) -> None:
    async def steps(ctx: Context) -> None:
        with pytest.raises(HTTPTimeout):
            await ctx.get(HTTP).send(Request("GET", f"{ctx.get(SERVER_ADDRESS)}/slow", timeout=0.05))

    outcome = await run_scenario([
        Entry("env", lambda: environment({})), Entry("routes", route_table),
        Entry("server", asyncio_http_server, config={"port": 0}), Entry("http", client), Entry("app", slow_route),
    ], steps, requires=(HTTP, SERVER_ADDRESS), grace=0.1)
    assert [error for _, error in outcome.failures if isinstance(error, HTTPError)] == []

def test_the_timeout_comes_from_the_environment_when_requests_do_not_say() -> None:
    from plugins import default_timeout
    assert default_timeout(lambda name: "2.5") == 2.5
    with pytest.raises(ValueError, match="more than zero"):
        default_timeout(lambda name: "0")
