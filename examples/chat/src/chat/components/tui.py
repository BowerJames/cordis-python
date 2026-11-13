import asyncio
import json
from contextlib import suppress
from typing import Any, ClassVar, Sequence
from urllib.parse import urlencode

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Markdown, Select, Static
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from cordis import Component, Context
from cordis_plugins import SERVER_ADDRESS

from ..contracts import AGENT_TYPES, PROVIDERS

# How many lines of a tool's output are shown; the model always gets all of it.
OUTPUT_LINES = 8


def _clipped(output: str) -> str:
    lines = output.splitlines()
    if len(lines) <= OUTPUT_LINES:
        return output
    return "\n".join([*lines[:OUTPUT_LINES], f"… {len(lines) - OUTPUT_LINES} more lines"])


class ChatApp(App[None]):
    # A client of the chat route, like any other: it opens a websocket for the provider, model and agent type chosen
    # at the top, sends what is typed at the bottom, and shows every event the session sends back.

    TITLE = "chat-server"
    CSS = """
    #setup { height: auto; padding: 0 1; }
    #setup Select { width: 22; }
    #setup Input { width: 1fr; }
    #transcript { padding: 0 1; }
    #transcript > * { margin: 1 0 0 0; }
    .you { color: $accent; text-style: bold; }
    .info { color: $text-muted; }
    .call { color: $warning; }
    .result { color: $text-muted; padding: 0 0 0 2; }
    .note { color: $secondary; text-style: italic; }
    .error { color: $error; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+n", "new_session", "New session"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, uri: str, providers: Sequence[str], agent_types: Sequence[str], provider: str | None = None,
                 model_name: str = "", agent_type: str | None = None) -> None:
        super().__init__()
        self.uri = uri
        self.providers = list(providers)
        self.agent_types = list(agent_types)
        self.chosen = (provider if provider in self.providers else self.providers[0] if self.providers else None,
                       model_name,
                       agent_type if agent_type in self.agent_types else self.agent_types[0] if self.agent_types else None)
        self.socket: ClientConnection | None = None
        self.working = False

    def compose(self) -> ComposeResult:
        provider, model_name, agent_type = self.chosen
        yield Header()
        with Horizontal(id="setup"):
            yield Select([(name, name) for name in self.providers], value=provider or Select.NULL, allow_blank=False,
                         id="provider")
            yield Input(model_name, placeholder="model name, such as glm-5.3 or gpt-6-luna", id="model")
            yield Select([(slug, slug) for slug in self.agent_types], value=agent_type or Select.NULL, allow_blank=False,
                         id="agent_type")
            yield Button("Connect", variant="primary", id="connect")
        yield VerticalScroll(id="transcript")
        yield Static("not connected: choose a provider, model and agent type, then connect", id="status")
        yield Input(placeholder="Say something to the agent", id="message")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.uri
        self.query_one("#model" if not self.chosen[1] else "#message", Input).focus()

    def show(self, widget: Widget) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.mount(widget)
        transcript.scroll_end(animate=False)

    def status(self, text: str) -> None:
        self.query_one("#status", Static).update(Text(text))

    def action_new_session(self) -> None:
        self.query_one("#model", Input).focus()

    @on(Button.Pressed, "#connect")
    @on(Input.Submitted, "#model")
    def connect_session(self) -> None:
        provider = self.query_one("#provider", Select).value
        model_name = self.query_one("#model", Input).value.strip()
        agent_type = self.query_one("#agent_type", Select).value
        if not model_name:
            self.notify("Give the model a name first", severity="warning")
            self.query_one("#model", Input).focus()
            return
        query = urlencode({"provider": provider, "model_name": model_name, "agent_type": agent_type})
        self.query_one("#transcript", VerticalScroll).remove_children()
        self.working = False
        # Exclusive: connecting again closes the session before.
        self.run_worker(self.session(f"{self.uri}?{query}"), exclusive=True, group="session")
        self.query_one("#message", Input).focus()

    @on(Input.Submitted, "#message")
    async def say(self, submitted: Input.Submitted) -> None:
        text = submitted.value.strip()
        if not text:
            return
        if self.socket is None:
            self.notify("Connect to a session first", severity="warning")
            return
        if self.working:
            self.notify("The agent is still working on your last message", severity="warning")
            return
        submitted.input.clear()
        self.show(Static(Text(f"you: {text}"), classes="you"))
        self.working = True
        self.status("working…")
        await self.socket.send(json.dumps({"type": "message", "text": text}))

    async def session(self, uri: str) -> None:
        self.status("connecting…")
        socket: ClientConnection | None = None
        try:
            async with connect(uri) as socket:
                self.socket = socket
                with suppress(ConnectionClosed):
                    async for text in socket:
                        self.received(json.loads(text))
        except OSError as error:
            self.show(Static(Text(f"could not connect to {self.uri}: {error}"), classes="error"))
        finally:
            self.socket = None
            self.working = False
        if socket is not None:
            reason = f": {socket.close_reason}" if socket.close_reason else ""
            self.show(Static(Text(f"session closed ({socket.close_code}{reason})"), classes="info"))
        self.status("not connected: ctrl+n to start a new session")

    def received(self, event: dict[str, Any]) -> None:
        match event.get("type"):
            case "ready":
                tools = ", ".join(event["tools"])
                self.ready = f"{event['provider']}/{event['model_name']} · {event['agent_type']} · tools: {tools}"
                self.show(Static(Text(f"connected to {self.ready}"), classes="info"))
                self.status(self.ready)
            case "assistant":
                self.show(Markdown(event["text"]))
            case "tool_call":
                self.show(Static(Text(f"→ {event['name']}({event['arguments']})"), classes="call"))
            case "tool_result":
                self.show(Static(Text(_clipped(event["output"])), classes="result"))
            case "note":
                self.show(Static(Text(f"note: {event['text']}"), classes="note"))
            case "error":
                self.show(Static(Text(event["message"]), classes="error"))
            case "done":
                self.working = False
                self.status(self.ready)
            case _:
                self.show(Static(Text(json.dumps(event)), classes="info"))


def tui(path: str = "/ws/chat", provider: str | None = None, model_name: str = "", agent_type: str | None = None) -> Component:
    # A terminal client for the chat route, offering the providers and agent types the server has as it starts, so
    # its entrypoint loads it once the rest has settled. It talks to the server over a websocket like any other client.
    # The client is this component's work, done when it is quit: its entrypoint runs for as long as that work does,
    # though the server would serve on. It requires the server's address, so it closes before the server stops.
    def apply(ctx: Context) -> None:
        async def run(stopping: asyncio.Event) -> None:
            address = ctx.get(SERVER_ADDRESS).replace("http://", "ws://", 1)
            app = ChatApp(f"{address}{path}", providers=ctx.get(PROVIDERS).names(), agent_types=ctx.get(AGENT_TYPES).names(),
                          provider=provider, model_name=model_name, agent_type=agent_type)
            running = asyncio.ensure_future(app.run_async())
            ending = asyncio.ensure_future(stopping.wait())
            try:
                await asyncio.wait({running, ending}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                ending.cancel()
            if not running.done():
                # Told to stop for some other reason, such as a signal: close the app, and give the terminal back.
                app.exit()
            await running

        ctx.spawn(run, "terminal client")

    return Component("tui", apply=apply, requires=(SERVER_ADDRESS, PROVIDERS, AGENT_TYPES))
