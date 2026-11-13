# The chat server with a terminal client of its own: `uv run chat-tui`. The client talks to the server over a
# websocket like any other, and the run is for the client: quitting it with ctrl+q ends the run. Sessions are not
# logged, as the client has the terminal. `uv run chat-tui --describe` shows how it fits together, without starting it.
import argparse
import asyncio
import sys
from typing import Sequence

from chat.components.tui import tui
from cordis_loader import Entry, describe, patched

from . import server

# The entry the run is for: loaded once the server has settled, and the run is done when it is.
TUI_ENTRY = "tui"

def compose(port: int | None = None, provider: str | None = None, model_name: str = "",
            agent_type: str | None = None) -> list[Entry]:
    # The server's composition, without the session log and with the terminal client, which starts with the provider,
    # model and agent type given, if any.
    return patched(server.compose(port=port, log_sessions=False),
                   Entry(TUI_ENTRY, tui, config={"provider": provider, "model_name": model_name, "agent_type": agent_type}))

def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chat-tui", description="An agent chat server, with a terminal client.")
    parser.add_argument("--describe", action="store_true", help="show how it fits together, without starting it")
    parser.add_argument("--port", type=int, help="the port to serve on (default 8000, or HTTP_PORT)")
    parser.add_argument("--provider", help="the provider the client starts with")
    parser.add_argument("--model", default="", help="the model name the client starts with")
    parser.add_argument("--agent-type", help="the agent type the client starts with")
    arguments = parser.parse_args(argv)

    entries = compose(port=arguments.port, provider=arguments.provider, model_name=arguments.model,
                      agent_type=arguments.agent_type)
    if arguments.describe:
        print(describe(server.with_a_session(entries)))
        return
    sys.exit(asyncio.run(server.run(entries, prog="chat-tui", last=TUI_ENTRY)))

if __name__ == "__main__":
    main()
