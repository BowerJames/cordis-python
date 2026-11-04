from cordis import Component, Context
from cordis_plugins import ROUTES, Route, ServerRequest, ServerResponse


async def echoed(request: ServerRequest) -> ServerResponse:
    # The body comes back exactly as it was sent, and is described the same way.
    content_type = request.header("Content-Type") or "application/octet-stream"
    return ServerResponse(status=200, headers=(("Content-Type", content_type),), body=request.body)

def echo() -> Component:
    # POST /echo, for as long as this is loaded: the route is its one effect, so unloading it withdraws the route, and a
    # running server stops answering it from the next request.
    def apply(ctx: Context) -> None:
        route = Route("POST", "/echo", echoed)
        ctx.effect(lambda: ctx.get(ROUTES).add(route), f"route {route}")
    return Component("echo", apply=apply, requires=(ROUTES,))
