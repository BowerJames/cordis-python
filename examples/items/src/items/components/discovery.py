import asyncio
from dataclasses import dataclass

from cordis import Component, Context
from cordis_plugins import ROUTES, SERVER_ADDRESS, STDOUT, Route, ServerRequest, ServerResponse


@dataclass(slots=True)
class DiscoveryState:
    address: str = ""
    registered: bool = False

def discovery() -> Component:
    # Serves its own status, and registers with discovery while a server is answering: the registration is a child
    # requiring the server's address, so it begins once the server has bound, and it is unloaded, and so told to stop,
    # and deregisters, before the server stops accepting. That order follows what relies on what; nothing here
    # arranges it. Staying registered is not what the program runs for, so it holds nothing.
    state = DiscoveryState()

    def registration(ctx: Context) -> None:
        state.address = ctx.get(SERVER_ADDRESS)

        async def registered(stopping: asyncio.Event) -> None:
            state.registered = True
            ctx.get(STDOUT)(f"registered {state.address} with discovery\n")
            await stopping.wait()
            state.registered = False
            ctx.get(STDOUT)("deregistered from discovery\n")

        ctx.spawn(registered, "registration", hold=False)

    def apply(ctx: Context) -> None:
        async def status(request: ServerRequest) -> ServerResponse:
            return ServerResponse.json({"address": state.address, "registered": state.registered})

        ctx.effect(lambda: ctx.get(ROUTES).add(Route("GET", "/discovery", status)), "route GET /discovery")
        ctx.inject([SERVER_ADDRESS, STDOUT], registration, name="registration")

    return Component("discovery", apply=apply, requires=(ROUTES,))
