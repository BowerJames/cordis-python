import asyncio
from typing import Any, Awaitable, Callable, Sequence

from cordis import Component, Context, Key
from cordis_loader import Entry, patched

from .components import Outcome, run_application
from .contracts import APPLICATION, serve

# For testing compositions as they run: a component that, once the application has started, runs steps with the ctx it
# was given, then ends the application. Whatever the steps need, the component requires, so it starts only once that
# is all there.

type Steps = Callable[[Context], Awaitable[None]]

def scenario(steps: Steps, requires: Sequence[Key[Any]] = ()) -> Component:
    def apply(ctx: Context) -> None:
        async def go(stopping: asyncio.Event) -> None:
            try:
                await steps(ctx)
            finally:
                ctx.get(APPLICATION).end(0)
        serve(ctx, "scenario", go)
    return Component("scenario", apply=apply, requires=(APPLICATION, *requires))

async def run_scenario(entries: Sequence[Entry], steps: Steps, requires: Sequence[Key[Any]] = (),
                       grace: float = 5.0) -> Outcome:
    # Runs entries with a scenario added, and gives the outcome; a failure in the steps is among its failures.
    return await run_application(patched(entries, Entry("scenario", lambda: scenario(steps, requires))),
                                 grace=grace, signals=False)
