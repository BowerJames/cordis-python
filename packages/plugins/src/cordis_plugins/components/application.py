import asyncio
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import Callable, Sequence

from cordis import Component, Context, Entries, Fiber, Inverse, Undo, open_runtime
from cordis_loader import Entry, Loader
from cordis_loader import describe as describe_entries

from ..contracts import APPLICATION, Application


class ApplicationRun:
    # The run as its runner keeps it: what APPLICATION offers the components, and what only the runner uses, such as
    # starting the run.
    __slots__ = ("_containers", "_started_callbacks", "ended", "failures", "started", "status")

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.ended = asyncio.Event()
        self.status: int | None = None
        self.failures: list[tuple[str, Exception]] = []
        self._started_callbacks: Entries[Callable[[], None]] = Entries()
        self._containers: dict[Fiber, Callable[[Exception, str], None]] = {}

    def when_started(self, callback: Callable[[], None]) -> Undo:
        if self.started.is_set():
            callback()
            return Undo(lambda: None, "when started, once started")
        return self._started_callbacks.add(callback, "when started")

    async def wait_started(self) -> bool:
        if self.started.is_set() or self.ended.is_set():
            return self.started.is_set()
        started = asyncio.ensure_future(self.started.wait())
        ended = asyncio.ensure_future(self.ended.wait())
        try:
            await asyncio.wait({started, ended}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            started.cancel()
            ended.cancel()
        return self.started.is_set()

    def start(self) -> None:
        self.started.set()
        for callback in self._started_callbacks:
            callback()

    def end(self, status: int = 0) -> None:
        if self.status is None:
            self.status = status
        self.ended.set()

    def contain(self, fiber: Fiber, handler: Callable[[Exception, str], None]) -> Undo:
        self._containers[fiber] = handler

        def release() -> None:
            if self._containers.get(fiber) is handler:
                del self._containers[fiber]

        return Undo(release, f"contain {fiber.name}")

    def fail(self, error: Exception, where: str, fiber: Fiber | None = None) -> None:
        # Reports a failure where it happened: to the handler containing fiber, if any, or else to the application.
        while fiber is not None:
            if fiber in self._containers:
                self._containers[fiber](error, where)
                return
            fiber = fiber.parent
        error.add_note(f"in {where}")
        self.failures.append((where, error))
        self.end(1)

def application(run: ApplicationRun) -> Component[None]:
    # The run is not configuration, which an entry could describe, but the runner's own handle on this one run: it is
    # bound into the effect function, so each run's component is one of its own.
    def apply(ctx: Context) -> Inverse:
        return ctx.provide(APPLICATION, Application(when_started=run.when_started, started=run.wait_started, end=run.end,
                                                    fail=lambda error, where: run.fail(error, where),
                                                    contain=run.contain))
    return Component("application", apply, provides=(APPLICATION,))

APPLICATION_ENTRY = "application"

def with_application(entries: Sequence[Entry], run: ApplicationRun) -> list[Entry]:
    # The entries an application runs: the one providing APPLICATION first, then the composition's own.
    return [Entry(APPLICATION_ENTRY, application(run)), *entries]

def describe(entries: Sequence[Entry]) -> str:
    # How an application's entries fit together, as run_application would load them, without loading or running any.
    return describe_entries(with_application(entries, ApplicationRun()))

@dataclass(slots=True, frozen=True)
class Outcome:
    # How an application ended: the status it ended with, and every failure it reported, with where it happened.
    status: int
    failures: tuple[tuple[str, Exception], ...]

    def failed_with[E: Exception](self, kind: type[E]) -> list[E]:
        return [error for _, error in self.failures if isinstance(error, kind)]

async def run_application(entries: Sequence[Entry], *, signals: bool = True) -> Outcome:
    # Loads entries, starts the application once they have quiesced, and runs it until something ends it; then closes
    # the runtime, which unloads every fiber, dependents first. A first SIGINT or SIGTERM ends it gracefully.
    app = ApplicationRun()

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
            await app.ended.wait()
        finally:
            for number in installed:
                loop.remove_signal_handler(number)
    status = app.status if app.status is not None else 0
    return Outcome(status=1 if app.failures and status == 0 else status, failures=tuple(app.failures))
