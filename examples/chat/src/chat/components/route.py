import json
from itertools import count
from typing import AsyncIterator, Iterator, Sequence

from cordis import Component, Context, Fiber, FiberState, Inverse, Runtime
from cordis_loader import Entry, Loader
from cordis_plugins import APPLICATION, ROUTES, Socket, SocketRoute

from ..contracts import (
    AGENT_TYPES,
    CONNECTION,
    PROVIDERS,
    SESSION_KEYS,
    SESSION_LOG,
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
from .catalog import catalog


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
    # It offers the catalogs of providers and agent types, knows nothing of what a session loads, and tells a session
    # log of each session's opening and closing when there is one, working the same without one.
    sessions = count(1)

    def apply(ctx: Context) -> Iterator[Inverse]:
        providers: Catalog[Provider] = catalog(lambda provider: provider.name)
        agent_types: Catalog[AgentProfile] = catalog(lambda profile: profile.slug)
        yield ctx.provide(PROVIDERS, providers)
        yield ctx.provide(AGENT_TYPES, agent_types)
        app = ctx.get(APPLICATION)

        def logging(connection: Connection) -> Component:
            # An optional dependency on the log, as a fiber of its own (outside the session, which is checked for what
            # it waits on): opening is told as it activates, and closing is its inverse, read through the log it
            # committed to, which therefore stays until this has run.
            def apply(inner: Context) -> Inverse:
                inner.get(SESSION_LOG).opened(SessionOpened(connection.number, connection.request))
                return lambda: inner.get(SESSION_LOG).closed(
                    SessionClosed(connection.number, connection.request, tuple(connection.failures)))
            return Component(f"session {connection.number} log", apply=apply, requires=(SESSION_LOG,))

        async def run_session(connection: Connection) -> None:
            scoped = ctx
            for key in SESSION_KEYS:
                scoped = scoped.isolate(key)

            async def open_session(group: Context) -> AsyncIterator[Inverse]:
                yield group.provide(CONNECTION, connection)
                tools: Catalog[Tool] = catalog(lambda tool: tool.spec.name)
                yield group.provide(TOOLS, tools)
                # The loader adopts each entry's fiber onto the group, which retires them all as it unloads.
                await Loader(group).load(session)

            # A session opens long after chat activated, so there is nothing to yield its inverse to: chat adopts it,
            # and disposing the session when it ends takes it off again, so ended sessions leave nothing behind.
            fiber = scoped.use(Component(f"session {connection.number}", apply=open_session,
                                         provides=(CONNECTION, TOOLS)))
            ctx.adopt(fiber.retire)

            def failed(error: Exception, where: str) -> None:
                connection.failures.append(f"{error} (in {where})")
                connection.finished.set()

            release = ctx.adopt(app.contain(fiber, failed))
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
            told = ctx.use(logging(connection))
            ctx.adopt(told.retire)
            try:
                await run_session(connection)
                if connection.failures:
                    await refuse(1011, *connection.failures)
                else:
                    await socket.close(1000, "session ended")
            finally:
                await told.dispose()

        yield ctx.get(ROUTES).add_socket(SocketRoute(path, handle))

    return Component("chat", apply=apply, requires=(APPLICATION, ROUTES), provides=(PROVIDERS, AGENT_TYPES))
