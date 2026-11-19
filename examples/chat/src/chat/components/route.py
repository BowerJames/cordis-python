import json
from dataclasses import dataclass
from itertools import count
from typing import AsyncIterator, Iterator, Sequence

from cordis import Component, Context, Inverse
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


@dataclass(frozen=True, slots=True)
class ChatConfig:
    # The entries each session loads beneath it, and the path the route serves.
    session: Sequence[Entry] = ()
    path: str = "/ws/chat"

def _chat(ctx: Context, config: ChatConfig) -> Iterator[Inverse]:
    sessions = count(1)
    # What a session opened from here loads; a new configuration may replace it while chat runs.
    template = config.session
    # The loader of each session that has loaded, by number, so a new template reaches it.
    live: dict[int, Loader] = {}

    providers: Catalog[Provider] = catalog(lambda provider: provider.name)
    agent_types: Catalog[AgentProfile] = catalog(lambda profile: profile.slug)
    yield ctx.provide(PROVIDERS, providers)
    yield ctx.provide(AGENT_TYPES, agent_types)
    app = ctx.get(APPLICATION)

    def logging(connection: Connection) -> Component[None]:
        # An optional dependency on the log, as a fiber of its own (outside the session, which is checked for what
        # it waits on): opening is told as it activates, and closing is its inverse, read through the log it
        # committed to, which therefore stays until this has run.
        def apply(inner: Context) -> Inverse:
            inner.get(SESSION_LOG).opened(SessionOpened(connection.number, connection.request))
            return lambda: inner.get(SESSION_LOG).closed(
                SessionClosed(connection.number, connection.request, tuple(connection.failures)))
        return Component(f"session {connection.number} log", apply, requires=(SESSION_LOG,))

    async def run_session(connection: Connection) -> None:
        scoped = ctx
        for key in SESSION_KEYS:
            scoped = scoped.isolate(key)

        async def open_session(group: Context) -> AsyncIterator[Inverse]:
            yield group.provide(CONNECTION, connection)
            tools: Catalog[Tool] = catalog(lambda tool: tool.spec.name)
            yield group.provide(TOOLS, tools)
            # The loader adopts each entry's fiber onto the group, which retires them all as it unloads.
            loader = Loader(group)
            await loader.load(template)
            live[connection.number] = loader

            def forget() -> None:
                live.pop(connection.number, None)

            yield forget
            # A template taken while this session was loading the one before it is loaded now.
            if list(loader.entries) != list(template):
                await loader.load(template)

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
            await fiber.settled()
            if not connection.finished.is_set():
                missing = fiber.unsatisfied()
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
            await refuse(4400, f"{config.path} needs {', '.join(missing)} in the query")
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

    async def accept(new: ChatConfig) -> bool:
        # A new path is a new route, which takes a reload. A new template is taken in place: sessions opened from
        # here load it, and each live session is reconciled to it as a group is (Section 5.2.1), keeping every
        # entry the two templates share, so every session ends where one opened with the new template would.
        nonlocal template
        if new.path != config.path:
            return False
        template = new.session
        for loader in tuple(live.values()):
            await loader.load(template)
        return True

    yield ctx.get(ROUTES).add_socket(SocketRoute(config.path, handle))
    yield ctx.accept(accept)

# Serves its path: each connection opens a session, for the provider and agent type named in the query, loading the
# session's entries beneath it. Every key a session binds for itself is isolated into a realm of the session's own, so
# sessions sit side by side, each with its own connection, model, state and tools, and none sees another's. It offers
# the catalogs of providers and agent types, knows nothing of what a session loads, and tells a session log of each
# session's opening and closing when there is one, working the same without one.
chat = Component("chat", _chat, config=ChatConfig, requires=(APPLICATION, ROUTES), provides=(PROVIDERS, AGENT_TYPES))
