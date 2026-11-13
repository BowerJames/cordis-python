import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping

import pytest

from chat.components import workspace
from chat.contracts import PROVIDERS, AssistantMessage, Model, Note, Provider, ToolCall, Turn
from chat.entrypoints import tui
from chat.entrypoints.server import compose, run_until_done, with_a_session
from cordis import Component, Context
from cordis_loader import Entry, describe, patched, without
from cordis_plugins import ROUTES, SERVER_ADDRESS, Socket, SocketHandler, capturing_stdio, environment


class Client:
    # One websocket connection to /ws/chat, held in memory: what the client says, and every event it is sent.
    def __init__(self, handle: SocketHandler, query: Mapping[str, str]) -> None:
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.closed: tuple[int, str] | None = None

        async def send(text: str) -> None:
            await self.events.put(json.loads(text))

        async def close(code: int, reason: str) -> None:
            if self.closed is None:
                self.closed = (code, reason)
                await self.events.put({"type": "closed", "code": code})
                # Closing takes a moment, as it does over a network, before the handler carries on.
                await asyncio.sleep(0.01)

        socket = Socket(path="/ws/chat", query=tuple(query.items()), headers=(), receive=self.inbox.get, send=send, close=close)
        async def handled() -> None:
            await handle(socket)

        self.task = asyncio.create_task(handled())

    async def say(self, text: str) -> None:
        await self.inbox.put(json.dumps({"type": "message", "text": text}))

    async def until(self, kind: str) -> list[dict[str, Any]]:
        # Every event up to and including the next of this kind.
        seen = [await self.events.get()]
        while seen[-1]["type"] not in (kind, "closed"):
            seen.append(await self.events.get())
        return seen

    async def leave(self) -> None:
        await self.inbox.put(None)
        await self.task

type Connect = Callable[[Mapping[str, str]], Client]

def memory_server(conversation: Callable[[Connect], Awaitable[None]]) -> Component:
    # Stands in for the HTTP server without a socket: hands /ws/chat connections made in memory to the socket route in
    # the table. The conversation is the work the run is for, so the run is done when it is; and, like a real server,
    # it lets its connections finish as it is told to stop.
    def apply(ctx: Context) -> None:
        ctx.provide(SERVER_ADDRESS, "memory://")
        clients: list[Client] = []

        def connect(query: Mapping[str, str]) -> Client:
            route = ctx.get(ROUTES).socket("/ws/chat")
            assert route is not None
            clients.append(Client(route.handle, query))
            return clients[-1]

        async def serving(stopping: asyncio.Event) -> None:
            await stopping.wait()
            await asyncio.gather(*(client.task for client in clients))

        async def talking(stopping: asyncio.Event) -> None:
            await conversation(connect)

        ctx.spawn(serving, "memory server", hold=False)
        ctx.spawn(talking, "conversation")

    return Component("memory server", apply=apply, requires=(ROUTES,), provides=(SERVER_ADDRESS,))

def scripted(replies: list[AssistantMessage], turns: list[Turn]) -> Component:
    # A provider for tests: one more provider on offer, added as an entry of its own without touching anything else.
    @asynccontextmanager
    async def connect(model_name: str) -> AsyncIterator[Model]:
        async def respond(turn: Turn) -> AssistantMessage:
            turns.append(turn)
            return replies.pop(0)
        yield respond

    def apply(ctx: Context) -> None:
        ctx.effect(lambda: ctx.get(PROVIDERS).offer(Provider("scripted", connect)))

    return Component("scripted", apply=apply, requires=(PROVIDERS,))

async def composed(conversation: Callable[[Connect], Awaitable[None]], root: Path,
                   replies: list[AssistantMessage] | None = None, turns: list[Turn] | None = None,
                   env: Mapping[str, str] = {}, stdio: Callable[[], Component] | None = None,
                   entries: list[Entry] | None = None) -> int:
    # The real chat server, run as its entrypoint runs it, with memory in place of the network, the terminal, the
    # environment and the models: each swapped by its entry. The run is for the server's conversation, which begins
    # once everything else has settled.
    failed = await run_until_done(patched(
        entries if entries is not None else compose(),
        Entry("stdio", stdio or capturing_stdio()[0]),
        Entry("env", lambda: environment(env)),
        Entry("server", lambda: memory_server(conversation)),
        Entry("workspace", lambda: workspace(root)),
        Entry("scripted", lambda: scripted(replies if replies is not None else [], turns if turns is not None else [])),
    ), last="server", signals=False)
    assert failed.failures == [], failed.failures
    return failed.status()

def calling(id: str, name: str, **arguments: Any) -> AssistantMessage:
    return AssistantMessage("", (ToolCall(id, name, json.dumps(arguments)),))

SCRIPTED = {"provider": "scripted", "model_name": "any", "agent_type": "explorer"}

@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "notes.txt").write_text("hello\nworld\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def greet():\n    return 'hello'\n")
    return tmp_path


def test_describe_shows_what_loads_in_each_session_and_what_is_on_offer() -> None:
    described = describe(with_a_session(compose()))
    assert ". read         after opening (tools in each-session), workspace (workspace), state (session_state in " \
           "each-session), harness (inject in each-session)" in described
    assert "  providers                      provided by chat; required by openai, zai, model" in described
    assert "  agent_types                    provided by chat; required by explorer, reader, harness" in described
    assert "  connection in each-session     provided by opening; required by model, harness" in described

def test_chat_only_uses_an_event_bus_and_the_terminal_client_goes_without_the_session_log() -> None:
    from chat.components import chat
    assert "events" not in {key.name for key in chat(session=[]).requires}
    described = describe(tui.compose())
    assert "tui" in described and "session-log" not in described

async def test_chat_works_the_same_without_an_event_bus(root: Path) -> None:
    replies = [AssistantMessage("hello")]
    seen: list[dict[str, Any]] = []

    async def conversation(connect: Connect) -> None:
        client = connect(SCRIPTED)
        await client.say("hi")
        seen.extend(await client.until("done"))
        await client.leave()

    await composed(conversation, root, replies, entries=without(compose(), "events", "session-log"))
    assert [event["type"] for event in seen] == ["ready", "assistant", "done"]

async def test_sessions_side_by_side_each_bind_their_keys_in_a_realm_of_their_own(root: Path) -> None:
    # Two sessions open at once: each has its own connection, model and tools, as realms keep them apart.
    replies = [AssistantMessage("one"), AssistantMessage("two")]
    seen: list[str] = []

    async def conversation(connect: Connect) -> None:
        first, second = connect(SCRIPTED), connect({**SCRIPTED, "agent_type": "reader"})
        ready = [await first.until("ready"), await second.until("ready")]
        seen.extend(",".join(events[-1]["tools"]) for events in ready)
        await first.say("a")
        await second.say("b")
        for client in (first, second):
            events = await client.until("assistant")
            seen.append(next(event["text"] for event in events if event["type"] == "assistant"))
        await first.leave()
        await second.leave()

    await composed(conversation, root, replies)
    assert seen[:2] == ["listDir,grep,read", "listDir,read"]
    assert sorted(seen[2:]) == ["one", "two"]


async def test_an_explorer_uses_its_tools_and_keeps_what_it_learns_for_the_session(root: Path) -> None:
    turns: list[Turn] = []
    replies = [calling("1", "listDir"), calling("2", "grep", pattern="hello", glob="*.py"),
               calling("3", "read", path="notes.txt"), calling("4", "read", path="notes.txt"),
               AssistantMessage("notes.txt says hello world")]
    seen: list[dict[str, Any]] = []

    async def conversation(connect: Connect) -> None:
        client = connect(SCRIPTED)
        seen.extend(await client.until("ready"))
        await client.say("what do the notes say?")
        seen.extend(await client.until("assistant"))
        await client.leave()
        seen.extend(await client.until("closed"))

    await composed(conversation, root, replies, turns)

    assert seen[0] == {"type": "ready", "provider": "scripted", "model_name": "any", "agent_type": "explorer",
                       "tools": ["listDir", "grep", "read"]}
    assert [(event["type"], event.get("name")) for event in seen[1:]] == [
        ("tool_call", "listDir"), ("tool_result", "listDir"),
        ("tool_call", "grep"), ("tool_result", "grep"),
        ("tool_call", "read"), ("tool_result", "read"),
        ("tool_call", "read"), ("tool_result", "read"),
        ("note", None), ("assistant", None), ("done", None), ("closed", None),
    ]
    assert seen[2]["output"] == "notes.txt\nsrc/"
    assert seen[4]["output"] == "src/app.py:2: return 'hello'"
    # The second read of an unchanged file is pointed out, from state the read tool kept between executions.
    assert seen[9]["text"] == "notes.txt was already read in this session and has not changed since."
    assert Note("notes.txt was already read in this session and has not changed since.") in turns[-1].items
    assert seen[-1] == {"type": "closed", "code": 1000}


async def test_each_session_has_state_of_its_own(root: Path) -> None:
    replies = [calling("1", "read", path="notes.txt"), AssistantMessage("first"),
               calling("1", "read", path="notes.txt"), AssistantMessage("second")]
    notes: list[str] = []

    async def conversation(connect: Connect) -> None:
        for _ in range(2):
            client = connect(SCRIPTED)
            await client.say("read the notes")
            notes.extend(event["text"] for event in await client.until("assistant") if event["type"] == "note")
            await client.leave()

    await composed(conversation, root, replies)
    assert notes == []


async def test_an_agent_type_only_has_its_own_tools(root: Path) -> None:
    replies = [calling("1", "grep", pattern="hello"), AssistantMessage("I cannot search")]
    seen: list[dict[str, Any]] = []

    async def conversation(connect: Connect) -> None:
        client = connect({**SCRIPTED, "agent_type": "reader"})
        await client.say("find hello")
        seen.extend(await client.until("assistant"))
        await client.leave()

    await composed(conversation, root, replies)

    assert seen[0]["tools"] == ["listDir", "read"]
    assert seen[2]["output"] == "error: grep is not one of this agent's tools: listDir, read"


async def test_tools_never_show_search_or_read_what_the_workspace_ignores(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text(".env\nbuild/\n")
    (tmp_path / ".env").write_text("ZAI_API_KEY=secret\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "config.py").write_text("ZAI_API_KEY = 'secret'\n")
    (tmp_path / "app.py").write_text("key = os.environ['ZAI_API_KEY']\n")
    replies = [calling("1", "listDir"), calling("2", "grep", pattern="ZAI_API_KEY"), calling("3", "read", path=".env"),
               calling("4", "read", path="build/config.py"), calling("5", "listDir", path="build"),
               calling("6", "read", path="missing/../.env"), AssistantMessage("nothing secret")]
    seen: list[dict[str, Any]] = []

    async def conversation(connect: Connect) -> None:
        client = connect(SCRIPTED)
        await client.say("find the key")
        seen.extend(event for event in await client.until("assistant") if event["type"] == "tool_result")
        await client.leave()

    await composed(conversation, tmp_path, replies)
    assert [event["output"] for event in seen] == [
        ".gitignore\napp.py",
        "app.py:1: key = os.environ['ZAI_API_KEY']",
        "error: .env is hidden from the tools, as the workspace ignores it",
        "error: build/config.py is hidden from the tools, as the workspace ignores it",
        "error: build is hidden from the tools, as the workspace ignores it",
        "error: missing/../.env is hidden from the tools, as the workspace ignores it",
    ]


async def test_tools_stay_inside_the_workspace(root: Path) -> None:
    replies = [calling("1", "read", path="../outside.txt"), AssistantMessage("no")]
    seen: list[dict[str, Any]] = []

    async def conversation(connect: Connect) -> None:
        client = connect(SCRIPTED)
        await client.say("read outside")
        seen.extend(await client.until("assistant"))
        await client.leave()

    await composed(conversation, root, replies)
    assert seen[2]["output"] == "error: ../outside.txt is outside the workspace"


async def test_an_unknown_provider_or_agent_type_or_a_missing_parameter_is_refused(root: Path) -> None:
    seen: list[list[dict[str, Any]]] = []

    async def conversation(connect: Connect) -> None:
        for query in ({**SCRIPTED, "provider": "nope"}, {**SCRIPTED, "agent_type": "nope"}, {"provider": "zai"}):
            seen.append(await connect(query).until("closed"))

    await composed(conversation, root)

    assert seen == [
        [{"type": "error", "message": "no provider is called nope; there are openai, scripted, zai"},
         {"type": "closed", "code": 4400}],
        [{"type": "error", "message": "no agent type is called nope; there are explorer, reader"},
         {"type": "closed", "code": 4400}],
        [{"type": "error", "message": "/ws/chat needs model_name, agent_type in the query"}, {"type": "closed", "code": 4400}],
    ]


async def test_a_missing_key_is_reported_at_startup_and_fails_only_sessions_that_need_it(root: Path) -> None:
    seen: list[list[dict[str, Any]]] = []
    stdio, captured = capturing_stdio()

    async def conversation(connect: Connect) -> None:
        seen.append(await connect({**SCRIPTED, "provider": "zai", "model_name": "glm-5.1"}).until("closed"))
        seen.append(await connect({**SCRIPTED, "provider": "openai", "model_name": "gpt-6-luna"}).until("closed"))
        working = connect(SCRIPTED)
        seen.append(await working.until("ready"))
        await working.leave()

    await composed(conversation, root, stdio=stdio)

    assert captured.stderr == ("OPENAI_API_KEY is not set, so sessions asking for openai will fail\n"
                               "ZAI_API_KEY is not set, so sessions asking for zai will fail\n")
    assert seen[0] == [{"type": "error", "message": "ZAI_API_KEY is not set (in model)"},
                       {"type": "closed", "code": 1011}]
    assert seen[1] == [{"type": "error", "message": "OPENAI_API_KEY is not set (in model)"},
                       {"type": "closed", "code": 1011}]
    assert seen[2][0]["type"] == "ready"


def sessions_logged(stdout: str) -> list[str]:
    # Sessions run side by side, so their lines may interleave: each session's stay in order, sessions by number.
    lines = [line for line in stdout.splitlines() if line.startswith("session ")]
    return sorted(lines, key=lambda line: int(line.split()[1]))

async def test_each_session_is_logged_as_it_opens_and_closes_with_why_it_failed(root: Path) -> None:
    stdio, captured = capturing_stdio()

    async def conversation(connect: Connect) -> None:
        await connect({**SCRIPTED, "provider": "zai", "model_name": "glm-5.1"}).until("closed")
        working = connect(SCRIPTED)
        await working.until("ready")
        await working.leave()

    await composed(conversation, root, stdio=stdio)
    assert sessions_logged(captured.stdout) == [
        "session 1 opened: zai/glm-5.1 as explorer",
        "session 1 failed: ZAI_API_KEY is not set (in model)",
        "session 2 opened: scripted/any as explorer",
        "session 2 closed",
    ]

async def test_a_refused_connection_opens_no_session_and_is_not_logged(root: Path) -> None:
    stdio, captured = capturing_stdio()

    async def conversation(connect: Connect) -> None:
        await connect({**SCRIPTED, "provider": "nope"}).until("closed")

    await composed(conversation, root, stdio=stdio)
    assert sessions_logged(captured.stdout) == []

async def test_a_session_ended_by_the_server_ending_is_still_logged_as_closed(root: Path) -> None:
    # The entrypoint unloads chat, and so ends its sessions, while the bus and the log listening to it are still
    # loaded.
    stdio, captured = capturing_stdio()

    async def conversation(connect: Connect) -> None:
        await connect(SCRIPTED).until("ready")

    await composed(conversation, root, stdio=stdio)
    assert sessions_logged(captured.stdout) == ["session 1 opened: scripted/any as explorer", "session 1 closed"]

async def test_ending_the_server_ends_open_sessions_first(root: Path) -> None:
    clients: list[Client] = []

    async def conversation(connect: Connect) -> None:
        clients.append(connect(SCRIPTED))
        await clients[0].until("ready")
        # Returning ends the run while the session is still open, as a signal would.

    await composed(conversation, root)
    assert clients[0].closed == (1000, "session ended")




def real(root: Path, replies: list[AssistantMessage], client: Callable[[Context], Awaitable[None]]) -> list[Entry]:
    # The real chat server on a free port, with a client component talking to it: the entry the run is for, loaded
    # once the server has settled.
    def talking() -> Component:
        def apply(ctx: Context) -> None:
            async def talk(stopping: asyncio.Event) -> None:
                await client(ctx)
            ctx.spawn(talk, "client")
        return Component("client", apply=apply, requires=(SERVER_ADDRESS,))

    return patched(compose(port=0), Entry("stdio", capturing_stdio()[0]), Entry("env", lambda: environment({})),
                   Entry("workspace", lambda: workspace(root)), Entry("scripted", lambda: scripted(replies, [])),
                   Entry("client", talking))

@pytest.mark.external
@pytest.mark.timeout(10)
async def test_the_server_serves_until_a_signal_ends_its_sessions_and_then_unloads(root: Path) -> None:
    # The entrypoint as shipped, with nothing named as what the run is for: the server holds the runtime until a
    # signal asks it to stop, and a session open then is ended, and logged as closed, before the rest unloads.
    import os
    import signal

    from websockets.asyncio.client import connect

    stdio, captured = capturing_stdio()
    closed: list[int | None] = []

    async def client(ctx: Context) -> None:
        address = ctx.get(SERVER_ADDRESS).replace("http://", "ws://")
        async with connect(f"{address}/ws/chat?provider=scripted&model_name=any&agent_type=reader") as socket:
            assert json.loads(await socket.recv())["type"] == "ready"
            os.kill(os.getpid(), signal.SIGINT)
            await socket.wait_closed()
            closed.append(socket.close_code)

    def talking() -> Component:
        def apply(ctx: Context) -> None:
            async def talk(stopping: asyncio.Event) -> None:
                await client(ctx)
            ctx.spawn(talk, "client", hold=False)
        return Component("client", apply=apply, requires=(SERVER_ADDRESS,))

    entries = patched(compose(port=0), Entry("stdio", stdio), Entry("env", lambda: environment({})),
                      Entry("workspace", lambda: workspace(root)), Entry("scripted", lambda: scripted([], [])),
                      Entry("client", talking))
    failed = await run_until_done(entries)
    assert failed.failures == []
    assert closed == [1000]
    assert sessions_logged(captured.stdout) == ["session 1 opened: scripted/any as reader", "session 1 closed"]
    assert captured.stdout.endswith("shutting down\n")

@pytest.mark.external
async def test_a_real_websocket_session_against_the_real_server(root: Path) -> None:
    from websockets.asyncio.client import connect

    events: list[dict[str, Any]] = []

    async def client(ctx: Context) -> None:
        address = ctx.get(SERVER_ADDRESS).replace("http://", "ws://")
        async with connect(f"{address}/ws/chat?provider=scripted&model_name=any&agent_type=reader") as socket:
            events.append(json.loads(await socket.recv()))
            await socket.send(json.dumps({"type": "message", "text": "list the files"}))
            while events[-1]["type"] != "assistant":
                events.append(json.loads(await socket.recv()))

    failed = await run_until_done(real(root, [calling("1", "listDir"), AssistantMessage("two entries")], client),
                                  last="client", signals=False)
    assert failed.failures == []
    assert [event["type"] for event in events] == ["ready", "tool_call", "tool_result", "assistant"]
    assert events[2]["output"] == "notes.txt\nsrc/"

@pytest.mark.external
@pytest.mark.timeout(30)  # starting a Textual app takes longer than the default
async def test_the_terminal_client_talks_to_an_agent_through_the_real_server(root: Path) -> None:
    from textual.containers import VerticalScroll
    from textual.widgets import Input, Markdown, Static

    from chat.components.tui import ChatApp

    # A running Textual app makes every task start eagerly; run_test does not, so it is done here, as in a terminal.
    asyncio.get_running_loop().set_task_factory(asyncio.eager_task_factory)
    shown: list[str] = []

    async def drive(ctx: Context) -> None:
        address = ctx.get(SERVER_ADDRESS).replace("http://", "ws://")
        app = ChatApp(f"{address}/ws/chat", providers=["openai", "zai", "scripted"], agent_types=["explorer", "reader"],
                      provider="scripted", model_name="any", agent_type="reader")
        async with app.run_test(size=(100, 30)) as pilot:
            async def until(ready: Callable[[], bool]) -> None:
                for _ in range(200):
                    if ready():
                        return
                    await pilot.pause(0.025)
                raise AssertionError("the terminal client never got there")

            await pilot.click("#connect")
            await until(lambda: app.socket is not None and not str(app.query_one("#status", Static).content)
                        .startswith("connecting"))
            app.query_one("#message", Input).value = "list the files"
            app.query_one("#message", Input).focus()
            await pilot.press("enter")
            await until(lambda: not app.working and bool(app.query(Markdown)))
            for widget in app.query_one("#transcript", VerticalScroll).children:
                shown.append(widget.source if isinstance(widget, Markdown) else str(widget.content)
                             if isinstance(widget, Static) else type(widget).__name__)

    failed = await run_until_done(real(root, [calling("1", "listDir"), AssistantMessage("There are **two** entries.")],
                                       drive), last="client", signals=False)
    assert failed.failures == []
    assert shown == [
        "connected to scripted/any · reader · tools: listDir, read",
        "you: list the files",
        "→ listDir({})",
        "notes.txt\nsrc/",
        "There are **two** entries.",
    ]
