import asyncio
from dataclasses import dataclass
from typing import Iterator

from cordis import Component, Context, Inverse
from cordis_plugins import APPLICATION, ROUTES, SERVER_ADDRESS, STDOUT, Route, ServerRequest, ServerResponse, background


@dataclass(slots=True)
class DiscoveryState:
    address: str = ""
    registered: bool = False

def discovery() -> Component:
    # Serves its own status, and registers with discovery while a server is answering: the registration is a child
    # requiring the server's address, so it begins once the server has bound, and as the application ends it is told
    # to stop, and deregisters, before the server stops accepting. That order follows what relies on what; nothing
    # here arranges it.
    state = DiscoveryState()

    def registration(ctx: Context) -> Inverse:
        state.address = ctx.get(SERVER_ADDRESS)

        async def registered(stopping: asyncio.Event) -> None:
            # Announced once everything has loaded, like the server's own address.
            if not await ctx.get(APPLICATION).started():
                return
            state.registered = True
            ctx.get(STDOUT)(f"registered {state.address} with discovery\n")
            await stopping.wait()
            state.registered = False
            ctx.get(STDOUT)("deregistered from discovery\n")

        return background(ctx, "registration", registered)

    def apply(ctx: Context) -> Iterator[Inverse]:
        async def status(request: ServerRequest) -> ServerResponse:
            return ServerResponse.json({"address": state.address, "registered": state.registered})

        yield ctx.get(ROUTES).add(Route("GET", "/discovery", status))
        yield ctx.inject([APPLICATION, SERVER_ADDRESS, STDOUT], registration, name="registration").retire

    return Component("discovery", apply=apply, requires=(ROUTES,))
