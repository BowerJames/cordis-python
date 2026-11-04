import asyncio

from cordis import Component, Context
from cordis_plugins import (
    APPLICATION,
    ROUTES,
    SERVER_ADDRESS,
    STDOUT,
    Route,
    ServerRequest,
    ServerResponse,
    serve,
)


async def healthy(request: ServerRequest) -> ServerResponse:
    return ServerResponse.text("ok")

async def echoed(request: ServerRequest) -> ServerResponse:
    # The body comes back exactly as it was sent, and is described the same way.
    content_type = request.header("Content-Type") or "application/octet-stream"
    return ServerResponse(status=200, headers=(("Content-Type", content_type),), body=request.body)

def _route(name: str, route: Route) -> Component:
    # A component that only adds a route: the route is its one effect, so unloading it withdraws the route, and a
    # running server stops answering it from the next request.
    def apply(ctx: Context) -> None:
        ctx.effect(lambda: ctx.get(ROUTES).add(route), f"route {route}")
    return Component(name, apply=apply, requires=(ROUTES,))

def health() -> Component:
    return _route("health", Route("GET", "/health", healthy))

def echo() -> Component:
    return _route("echo", Route("POST", "/echo", echoed))

def announce() -> Component:
    # Says where the server is once the application has started, and says when it is going away. It relies on the
    # server, through its address, so as the application ends it is told to stop before the server is: the farewell is
    # written while the server still serves, by the order ending follows, not by anything this component arranges.
    def apply(ctx: Context) -> None:
        async def announcing(stopping: asyncio.Event) -> None:
            write = ctx.get(STDOUT)
            write(f"listening on {ctx.get(SERVER_ADDRESS)}\n")
            for route in sorted(ctx.get(ROUTES).routes(), key=lambda route: (route.path, route.method)):
                write(f"  {route.method.upper():<6} {route.path}\n")
            await stopping.wait()
            write("shutting down\n")

        serve(ctx, "announce", announcing)

    return Component("announce", apply=apply, requires=(APPLICATION, SERVER_ADDRESS, ROUTES, STDOUT))
