# An agent chat server: `uv run chat-server`, then connect a websocket to
# ws://127.0.0.1:8000/ws/chat?provider=zai&model_name=glm-5.1&agent_type=explorer and send a message.
# Keys come from OPENAI_API_KEY and ZAI_API_KEY; tools look in WORKSPACE_ROOT, or the current directory.
# Each session is logged on stdout as it opens and closes. `uv run chat-tui` runs the same server with a terminal
# client of its own.
# `uv run chat-server --describe` shows how it fits together, application and session, without starting it.
import argparse
import asyncio
import sys
from contextlib import nullcontext
from typing import Sequence

from chat.components import (
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
from chat.contracts import CONNECTION, SESSION_KEYS, TOOLS
from cordis import Component, Context, open_runtime
from cordis_loader import Entry, Loader, describe, patched, without
from cordis_plugins import (
    StopOnFailure,
    announce,
    event_bus,
    fastapi_http_server,
    get_env_os,
    health,
    openai_sdk,
    route_table,
    stdio,
    stop_on_signals,
)

# The entry that opens the sessions, unloaded before the rest as the server ends.
CHAT_ENTRY = "chat"

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
        Entry(CHAT_ENTRY, chat, config={"session": sessions if sessions is not None else session()}),
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

async def run_until_done(entries: list[Entry], *, last: str | None = None, signals: bool = True) -> StopOnFailure:
    # Runs the composition until it is done, and gives what failed. The server holds the runtime for as long as it
    # serves, so by itself that is until a signal or a failure asks it to stop. Given the id of an entry as last, that
    # entry is what the run is for: it is loaded once the rest have settled, so it meets everything in place, and the
    # run is done when it is, though the server would serve on. The terminal client is run that way.
    failed = StopOnFailure()
    async with open_runtime(on_error=failed) as runtime:
        with stop_on_signals(runtime) if signals else nullcontext():
            loader = Loader(runtime.root)
            if last is None:
                await loader.load(entries)
                await runtime.done()
            else:
                await loader.load(without(entries, last))
                await runtime.settled()
                await loader.load(entries)
                await runtime.settled()
                await runtime.done(under=loader.fiber(last))
        # Open sessions end first, by unloading chat while everything else is still loaded: each session's close is
        # then heard by whatever listens for it, such as the session log. Nothing declares that order, since chat only
        # uses an event bus when there is one, so this entrypoint, which knows its composition, arranges it, as one
        # more configuration to reconcile to. Leaving the runtime's block then unloads the rest, dependents first.
        await loader.load(without(entries, CHAT_ENTRY))
    return failed

async def run(entries: list[Entry], prog: str = "chat-server", last: str | None = None) -> int:
    failed = await run_until_done(entries, last=last)
    for _, error in failed.failures:
        notes = "; ".join(getattr(error, "__notes__", []))
        sys.stderr.write(f"{prog}: {error}" + (f" ({notes})" if notes else "") + "\n")
    return failed.status()

def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chat-server", description="An agent chat server.")
    parser.add_argument("--describe", action="store_true", help="show how it fits together, without starting it")
    parser.add_argument("--port", type=int, help="the port to serve on (default 8000, or HTTP_PORT)")
    arguments = parser.parse_args(argv)

    entries = compose(port=arguments.port)
    if arguments.describe:
        print(describe(with_a_session(entries)))
        return
    sys.exit(asyncio.run(run(entries)))

if __name__ == "__main__":
    main()
