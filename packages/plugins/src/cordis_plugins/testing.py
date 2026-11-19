import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence

from cordis import Component, Context, Inverse, Key, open_runtime
from cordis_loader import Entry, patched

from .components import Outcome, run_application
from .contracts import APPLICATION, background

# For testing compositions as they run: a component that, once the application has started, runs steps with the ctx it
# was given, then ends the application. Whatever the steps need, the component requires, so it starts only once that
# is all there.

type Steps = Callable[[Context], Awaitable[None]]

def scenario(steps: Steps, requires: Sequence[Key[Any]] = ()) -> Component[None]:
    # Built for each test, since what it requires is part of the component (its d), and the steps are code.
    def apply(ctx: Context) -> Inverse:
        async def go(stopping: asyncio.Event) -> None:
            app = ctx.get(APPLICATION)
            if not await app.started():
                return
            try:
                await steps(ctx)
            finally:
                app.end(0)
        return background(ctx, "scenario", go)
    return Component("scenario", apply, requires=(APPLICATION, *requires))

async def run_scenario(entries: Sequence[Entry], steps: Steps, requires: Sequence[Key[Any]] = ()) -> Outcome:
    # Runs entries with a scenario added, and gives the outcome; a failure in the steps is among its failures.
    return await run_application(patched(entries, Entry("scenario", scenario(steps, requires))),
                                 signals=False)

@asynccontextmanager
async def provided[T](key: Key[T], component: Component[Any]) -> AsyncIterator[T]:
    # The value component binds at key, loaded on its own in a runtime of its own and unloaded afterwards: for testing
    # a contract against each component that keeps it, through the contract alone.
    async with open_runtime() as runtime:
        runtime.root.use(component)
        await runtime.settled()
        value = runtime.lookup(key)
        if value is None:
            raise LookupError(f"{component.name} provides nothing at {key.name}")
        yield value
