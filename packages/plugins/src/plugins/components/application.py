import asyncio
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import Sequence

from cordis import Component, Context, Fiber, Runtime, open_runtime
from cordis_loader import Entry, Loader
from cordis_loader import describe as describe_entries

from ..contracts import APPLICATION, Application


def application(app: Application) -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(APPLICATION, app)
    return Component("application", apply=apply, provides=(APPLICATION,))

APPLICATION_ENTRY = "application"

def with_application(entries: Sequence[Entry], app: Application) -> list[Entry]:
    # The entries an application runs: the one providing APPLICATION first, then the composition's own.
    return [Entry(APPLICATION_ENTRY, lambda: application(app)), *entries]

def describe(entries: Sequence[Entry]) -> str:
    # How an application's entries fit together, as run_application would load them, without loading or running any.
    return describe_entries(with_application(entries, Application()))

@dataclass(slots=True, frozen=True)
class Outcome:
    # How an application ended: the status it ended with, and every failure it reported, with where it happened.
    status: int
    failures: tuple[tuple[str, Exception], ...]

    def failed_with[E: Exception](self, kind: type[E]) -> list[E]:
        return [error for _, error in self.failures if isinstance(error, kind)]

def _relies(runtime: Runtime, dependent: Fiber, provider: Fiber) -> bool:
    # Whether dependent reaches provider through committed views: what it activated against, and what that did, on.
    seen: set[Fiber] = set()
    frontier = [dependent]
    while frontier:
        fiber = frontier.pop()
        for committed in (fiber.committed or {}).values():
            if committed is provider:
                return True
            if committed not in seen:
                seen.add(committed)
                frontier.append(committed)
        # A fiber relies on whatever its parent relies on: it was instantiated beneath it.
        if fiber.parent is not None and fiber.parent not in seen and fiber.parent is not runtime.root_fiber:
            seen.add(fiber.parent)
            frontier.append(fiber.parent)
    return False

async def _until_done(runtime: Runtime, app: Application) -> None:
    # Ends when something ends the application, or when, started and settled, nothing holds it open any more.
    while not app.ended.is_set():
        changed = app.changed
        if not app.held:
            await runtime.settled()
            if not app.held and not app.ended.is_set():
                return
            continue
        await changed.wait()

async def run_application(
    entries: Sequence[Entry],
    *,
    grace: float = 5.0,
    signals: bool = True,
) -> Outcome:
    # Loads entries, starts the application once everything has settled, and runs it until it is done; then closes the
    # runtime, so every fiber is unloaded, dependents first. A first SIGINT or SIGTERM ends it gracefully.
    app = Application(grace=grace)

    def on_error(fiber: Fiber, error: Exception) -> None:
        app.fail(error, fiber.name, fiber)

    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    async with open_runtime(on_error=on_error) as runtime:
        await Loader(runtime.root).load(with_application(entries, app))
        await runtime.settled()
        if signals:
            for number in (signal.SIGINT, signal.SIGTERM):
                with suppress(NotImplementedError, RuntimeError, ValueError):
                    loop.add_signal_handler(number, app.end, 0)
                    installed.append(number)
        try:
            if not app.ended.is_set():
                app.start()
            await _until_done(runtime, app)
            await app.stop_services(lambda dependent, provider: _relies(runtime, dependent, provider))
        finally:
            for number in installed:
                loop.remove_signal_handler(number)
    status = app.status if app.status is not None else 0
    return Outcome(status=1 if app.failures and status == 0 else status, failures=tuple(app.failures))
