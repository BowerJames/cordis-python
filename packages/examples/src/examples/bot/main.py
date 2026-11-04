import argparse
import asyncio
from typing import Any

from cordis import Runtime, open_runtime
from cordis_loader import Entry, Loader

from .contracts import CHANNEL

# A bot whose configuration changes while it runs. Each change goes through the loader, which rebuilds only what the
# change reaches; the transcript shows the components unloading and loading around the conversation.

def configuration(lines: list[str], greeting: str = "hello", counting: bool = True) -> list[Entry]:
    components = "examples.bot.components"
    return [
        Entry("events", "plugins.components.event_bus:event_bus"),
        Entry("channel", f"{components}:transcript_channel", config={"lines": lines}),
        Entry("features", children=[
            Entry("greeter", f"{components}:greeter", config={"greeting": greeting}),
            Entry("counter", f"{components}:counter", disabled=not counting),
        ]),
        Entry("store", f"{components}:memory_store"),
    ]

async def say(runtime: Runtime, user: str, text: str) -> None:
    channel = runtime.root.find(CHANNEL)
    assert channel is not None, "the channel is not running"
    await channel.receive(user, text)

async def run(describe_only: bool = False) -> list[str]:
    lines: list[str] = []
    async with open_runtime() as runtime:
        loader = Loader(runtime.root)
        if describe_only:
            return loader.describe(configuration(lines)).splitlines()
        await loader.load(configuration(lines))
        await runtime.settled()
        await say(runtime, "ada", "hi")
        await say(runtime, "ada", "count")
        # The greeter is revised with a new greeting; the counter is disabled, then brought back.
        await loader.load(configuration(lines, greeting="hey", counting=False))
        await runtime.settled()
        await say(runtime, "grace", "hi")
        await say(runtime, "grace", "count")
        await loader.load(configuration(lines, greeting="hey"))
        await runtime.settled()
        await say(runtime, "grace", "count")
    return lines

def main(argv: Any = None) -> None:
    parser = argparse.ArgumentParser(description="A bot reconfigured while it runs.")
    parser.add_argument("--describe", action="store_true", help="say how it fits together, without starting it")
    arguments = parser.parse_args(argv)
    for line in asyncio.run(run(describe_only=arguments.describe)):
        print(line)
