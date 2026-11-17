from cordis import Component, Context, Inverse
from cordis_plugins import ROUTES, Route, ServerRequest, ServerResponse


async def echoed(request: ServerRequest) -> ServerResponse:
    # The body comes back exactly as it was sent, and is described the same way.
    content_type = request.header("Content-Type") or "application/octet-stream"
    return ServerResponse(status=200, headers=(("Content-Type", content_type),), body=request.body)

def echo() -> Component:
    # POST /echo, for as long as this is loaded: the route is its one effect, and adding it gives back the inverse it
    # yields, so unloading it withdraws the route, and a running server stops answering it from the next request.
    def apply(ctx: Context) -> Inverse:
        return ctx.get(ROUTES).add(Route("POST", "/echo", echoed))
    return Component("echo", apply=apply, requires=(ROUTES,))
