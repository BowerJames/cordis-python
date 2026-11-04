import json
from itertools import count
from typing import Sequence

from cordis import Component, Context, Fiber, FiberState, Runtime
from cordis_loader import Entry, Loader
from cordis_plugins import APPLICATION, EVENTS, ROUTES, Socket, SocketRoute, Topic

from .catalog import catalog
from .contracts import (
    AGENT_TYPES,
    CONNECTION,
    PROVIDERS,
    SESSION_CLOSED,
    SESSION_KEYS,
    SESSION_OPENED,
    TOOLS,
    AgentProfile,
    Catalog,
    Connection,
    Provider,
    SessionClosed,
    SessionOpened,
    SessionRequest,
    Tool,
)


def _subtree(runtime: Runtime, fiber: Fiber) -> list[Fiber]:
    return [fiber, *(member for child in runtime.children(fiber) for member in _subtree(runtime, child))]

def _unsatisfied(runtime: Runtime, fiber: Fiber) -> list[str]:
    # What keeps any part of a settled session from activating: each key nothing in reach provides.
    return [f"{member.name} requires {key.name}, which nothing provides"
            for member in _subtree(runtime, fiber) if member.state is FiberState.INACTIVE
            for key in member.component.requires if runtime.provider(member.realms[key]) is None]

def chat(session: Sequence[Entry], path: str = "/ws/chat") -> Component:
    # Serves path: each connection opens a session, for the provider and agent type named in the query, loading the
    # session's entries beneath it. Every key a session binds for itself is isolated into a realm of the session's own,
    # so sessions sit side by side, each with its own connection, model, state and tools, and none sees another's.
    # It offers the catalogs of providers and agent types, knows nothing of what a session loads, and publishes each
    # session's opening and closing when there is an event bus, working the same without one.
    sessions = count(1)

    def apply(ctx: Context) -> None:
        providers: Catalog[Provider] = catalog(lambda provider: provider.name)
        agent_types: Catalog[AgentProfile] = catalog(lambda profile: profile.slug)
        ctx.provide(PROVIDERS, providers)
        ctx.provide(AGENT_TYPES, agent_types)
        app = ctx.get(APPLICATION)

        async def tell[T](topic: Topic[T], event: T) -> None:
            events = ctx.find(EVENTS)
            if events is not None:
                await events.publish(topic, event)

        async def run_session(connection: Connection) -> None:
            scoped = ctx
            for key in SESSION_KEYS:
                scoped = scoped.isolate(key)

            async def open_session(group: Context) -> None:
                group.provide(CONNECTION, connection)
                tools: Catalog[Tool] = catalog(lambda tool: tool.spec.name)
                group.provide(TOOLS, tools)
                await Loader(group).load(session)

            fiber = scoped.use(Component(f"session {connection.number}", apply=open_session,
                                         provides=(CONNECTION, TOOLS)))

            def failed(error: Exception, where: str) -> None:
                connection.failures.append(f"{error} (in {where})")
                connection.finished.set()

            release = app.contain(fiber, failed)
            try:
                await ctx.runtime.settled(under=fiber)
                if not connection.finished.is_set():
                    missing = _unsatisfied(ctx.runtime, fiber)
                    if missing:
                        connection.failures.extend(missing)
                        connection.finished.set()
                    else:
                        connection.ready.set()
                await connection.finished.wait()
            finally:
                await fiber.dispose()
                release()

        async def handle(socket: Socket) -> None:
            async def refuse(code: int, *messages: str) -> None:
                for message in messages:
                    await socket.send(json.dumps({"type": "error", "message": message}))
                await socket.close(code, messages[0][:120] if messages else "")

            query = dict(socket.query)
            missing = [name for name in ("provider", "model_name", "agent_type") if not query.get(name)]
            if missing:
                await refuse(4400, f"{path} needs {', '.join(missing)} in the query")
                return
            request = SessionRequest(provider=query["provider"], model_name=query["model_name"],
                                     agent_type=query["agent_type"])
            unknown = [
                f"no {kind} is called {asked}; there are {', '.join(sorted(offered))}"
                for kind, asked, offered in (
                    ("provider", request.provider, providers.names()),
                    ("agent type", request.agent_type, agent_types.names()),
                )
                if asked not in offered
            ]
            if unknown:
                await refuse(4400, *unknown)
                return
            connection = Connection(socket=socket, request=request, number=next(sessions))
            await tell(SESSION_OPENED, SessionOpened(connection.number, request))
            await run_session(connection)
            if connection.failures:
                await refuse(1011, *connection.failures)
            else:
                await socket.close(1000, "session ended")
            await tell(SESSION_CLOSED, SessionClosed(connection.number, request, tuple(connection.failures)))

        ctx.effect(lambda: ctx.get(ROUTES).add_socket(SocketRoute(path, handle)), f"socket route {path}")

    return Component("chat", apply=apply, requires=(APPLICATION, ROUTES), provides=(PROVIDERS, AGENT_TYPES))
