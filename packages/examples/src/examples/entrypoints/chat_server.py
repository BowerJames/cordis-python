# An agent chat server: `uv run chat-server`, then connect a websocket to
# ws://127.0.0.1:8000/ws/chat?provider=zai&model_name=glm-5.1&agent_type=explorer and send a message.
# Keys come from OPENAI_API_KEY and ZAI_API_KEY; tools look in WORKSPACE_ROOT, or the current directory.
# Each session is logged on stdout as it opens and closes.
# `uv run chat-server --tui` also runs a terminal client of the server, until it is quit with ctrl+q; sessions are not
# logged then, as the client has the terminal.
# `uv run chat-server --describe` shows how it fits together, application and session, without starting it.
import argparse
import asyncio
import sys
from typing import Sequence

from cordis import Component, Context
from cordis_loader import Entry, patched
from examples.chat import (
    chat,
    explorer,
    grep_tool,
    harness,
    list_dir_tool,
    model,
    openai_responses,
    read_tool,
    reader,
    session_log,
    session_state,
    workspace,
    zai_coding_plan,
)
from examples.chat.contracts import CONNECTION, SESSION_KEYS, TOOLS
from examples.chat.tui import tui
from examples.echo import announce, health
from plugins import (
    describe,
    event_bus,
    fastapi_http_server,
    get_env_os,
    openai_sdk,
    route_table,
    run_application,
    stdio,
)


def session() -> list[Entry]:
    # What each session loads, one of each per connection, beneath the session chat opens for it.
    return [
        Entry("state", session_state),
        Entry("model", model),
        Entry("harness", harness),
        Entry("read", read_tool),
        Entry("grep", grep_tool),
        Entry("list-dir", list_dir_tool),
    ]

def compose(port: int | None = None, log_sessions: bool = True, sessions: list[Entry] | None = None) -> list[Entry]:
    # The application: one of each, for as long as the server runs, with every provider and agent type on offer.
    entries = [
        Entry("stdio", stdio),
        Entry("env", get_env_os),
        Entry("routes", route_table),
        Entry("server", fastapi_http_server, config={"port": port}),
        Entry("health", health),
        Entry("announce", announce),
        Entry("workspace", workspace),
        Entry("chat", chat, config={"session": sessions if sessions is not None else session()}),
        Entry("openai-sdk", openai_sdk),
        Entry("openai", openai_responses),
        Entry("zai", zai_coding_plan),
        Entry("explorer", explorer),
        Entry("reader", reader),
        Entry("events", event_bus),
    ]
    return patched(entries, Entry("session-log", session_log)) if log_sessions else entries

def with_a_session(entries: list[Entry], sessions: list[Entry] | None = None) -> list[Entry]:
    # The application with one session placed in it as chat places each: a group isolating every key a session binds
    # for itself, opened by a session that provides its connection and tools. Only for describing: chat opens the real
    # ones as connections arrive.
    def opened(ctx: Context) -> None:
        pass
    opening = Entry("opening", lambda: Component("session", apply=opened, provides=(CONNECTION, TOOLS)))
    return [*entries, Entry("each-session", children=[opening, *(sessions if sessions is not None else session())],
                            isolate={key: True for key in SESSION_KEYS})]

async def run(entries: list[Entry]) -> int:
    outcome = await run_application(entries)
    for _, error in outcome.failures:
        notes = "; ".join(getattr(error, "__notes__", []))
        sys.stderr.write(f"chat-server: {error}" + (f" ({notes})" if notes else "") + "\n")
    return outcome.status

def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chat-server", description="An agent chat server, with a terminal client if asked.")
    parser.add_argument("--describe", action="store_true", help="show how it fits together, without starting it")
    parser.add_argument("--port", type=int, help="the port to serve on (default 8000, or HTTP_PORT)")
    parser.add_argument("--tui", action="store_true", help="also run a terminal client; quitting it stops the server")
    parser.add_argument("--provider", help="the provider the terminal client starts with")
    parser.add_argument("--model", default="", help="the model name the terminal client starts with")
    parser.add_argument("--agent-type", help="the agent type the terminal client starts with")
    arguments = parser.parse_args(argv)

    entries = compose(port=arguments.port, log_sessions=not arguments.tui)
    if arguments.tui:
        entries = patched(entries, Entry("tui", tui, config={"provider": arguments.provider, "model_name": arguments.model,
                                                             "agent_type": arguments.agent_type}))
    if arguments.describe:
        print(describe(with_a_session(entries)))
        return
    sys.exit(asyncio.run(run(entries)))

if __name__ == "__main__":
    main()
