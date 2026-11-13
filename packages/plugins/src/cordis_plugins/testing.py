import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence

from cordis import Component, Context, FiberState, Key, open_runtime
from cordis_loader import Entry, Loader

from .contracts import EXIT_STATUS
from .running import StopOnFailure

# For testing compositions as they run, each helper an entrypoint in small: it opens a runtime, loads the entries,
# waits for the runtime to be done, and closes it, as a program's own entrypoint does, without the signals.

type Steps = Callable[[Context], Awaitable[None]]

@dataclass(slots=True, frozen=True)
class Outcome:
    # How a run ended: the status it would exit with, and every failure reported, with the fiber it happened at.
    status: int
    failures: tuple[tuple[str, Exception], ...]

    def failed_with[E: Exception](self, kind: type[E]) -> list[E]:
        return [error for _, error in self.failures if isinstance(error, kind)]

async def run(entries: Sequence[Entry], *, then: Sequence[Entry] = (), grace: float = 5.0) -> Outcome:
    # Runs entries until the runtime is done: until nothing holds it, or a failure stops it. Entries given as then are
    # loaded once the rest have settled, as an entrypoint loads what must come after everything else, such as the
    # parser of a command line.
    failed = StopOnFailure()
    async with open_runtime(on_error=failed, grace=grace) as runtime:
        loader = Loader(runtime.root)
        await loader.load(entries)
        if then:
            await runtime.settled()
            await loader.load([*entries, *then])
        await runtime.done()
        given = runtime.root.find(EXIT_STATUS)
    return Outcome(status=failed.status(given), failures=tuple(failed.failures))

def scenario(steps: Steps, requires: Sequence[Key[Any]] = ()) -> Component:
    # Runs steps with the ctx it was given, holding the runtime while they run. Whatever the steps need, the component
    # requires, so it starts only once that is all there.
    def apply(ctx: Context) -> None:
        async def go(stopping: asyncio.Event) -> None:
            await steps(ctx)
        ctx.spawn(go, "scenario")
    return Component("scenario", apply=apply, requires=tuple(requires))

async def run_scenario(entries: Sequence[Entry], steps: Steps, requires: Sequence[Key[Any]] = (), *,
                       then: Sequence[Entry] = (), grace: float = 5.0) -> Outcome:
    # Runs entries and, once they have settled, steps beside them; the run is done when the steps are, whatever else
    # would have kept it going. A failure in the steps is among the outcome's failures. Entries given as then are
    # loaded once the rest have settled, and the steps run once they have settled too.
    failed = StopOnFailure()
    async with open_runtime(on_error=failed, grace=grace) as runtime:
        loader = Loader(runtime.root)
        await loader.load(entries)
        await runtime.settled()
        if then:
            await loader.load([*entries, *then])
            await runtime.settled()
        fiber = runtime.root.use(scenario(steps, requires))
        await runtime.settled()
        if fiber.state is not FiberState.ACTIVE and not failed.failures:
            missing = [key.name for key in requires if runtime.root.find(key) is None]
            raise LookupError(f"the scenario never started: nothing provides {', '.join(missing) or 'what it requires'}")
        await runtime.done(under=fiber)
        given = runtime.root.find(EXIT_STATUS)
    return Outcome(status=failed.status(given), failures=tuple(failed.failures))

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
