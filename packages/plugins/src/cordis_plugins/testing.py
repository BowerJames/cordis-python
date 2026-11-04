import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence

from cordis import Component, Context, Key, open_runtime
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

@asynccontextmanager
async def provided[T](key: Key[T], component: Callable[[], Component]) -> AsyncIterator[T]:
    # The value component binds at key, loaded on its own in a runtime of its own and unloaded afterwards: for testing
    # a contract against each component that keeps it, through the contract alone.
    async with open_runtime() as runtime:
        runtime.root.use(component())
        await runtime.settled()
        value = runtime.root.find(key)
        if value is None:
            raise LookupError(f"{component.__name__} provides nothing at {key.name}")
        yield value
