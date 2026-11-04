import asyncio
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import Callable, Sequence

from cordis import Component, Context, Effect, Entries, Fiber, Inverse, Runtime, open_runtime
from cordis_loader import Entry, Loader
from cordis_loader import describe as describe_entries

from ..contracts import APPLICATION, Application, Work


class ApplicationRun:
    # The run as its runner keeps it: what APPLICATION offers the components, and what only the runner uses, such as
    # starting the run and stopping what is served.
    __slots__ = ("_containers", "_holds", "_services", "_started_callbacks", "changed", "ended", "failures", "grace",
                 "started", "status")

    def __init__(self, grace: float = 5.0) -> None:
        self.started = asyncio.Event()
        self.ended = asyncio.Event()
        self.status: int | None = None
        self.failures: list[tuple[str, Exception]] = []
        # How long a served task is given to finish once told to stop, before it is cancelled.
        self.grace = grace
        # Set and replaced whenever a hold is taken or released.
        self.changed = asyncio.Event()
        self._holds: Entries[str] = Entries()
        self._started_callbacks: Entries[Callable[[], None]] = Entries()
        self._containers: dict[Fiber, Callable[[Exception, str], None]] = {}
        self._services: Entries[tuple[Fiber, asyncio.Event, asyncio.Task[None]]] = Entries()

    def when_started(self, callback: Callable[[], None]) -> Inverse:
        if self.started.is_set():
            callback()
            return lambda: None
        return self._started_callbacks.add(callback)

    def start(self) -> None:
        self.started.set()
        for callback in self._started_callbacks:
            callback()

    def hold(self, label: str) -> Inverse:
        release = self._holds.add(label)
        self._signal()

        def let_go() -> None:
            release()
            self._signal()

        return let_go

    @property
    def held(self) -> tuple[str, ...]:
        return tuple(self._holds)

    def end(self, status: int = 0) -> None:
        if self.status is None:
            self.status = status
        self.ended.set()
        self._signal()

    def _service(self, fiber: Fiber, stopping: asyncio.Event, task: asyncio.Task[None]) -> Inverse:
        # A task served by fiber, told to stop by stopping: registered until it finishes, so ending can stop it first.
        return self._services.add((fiber, stopping, task))

    async def stop_services(self, relies: Callable[[Fiber, Fiber], bool] = lambda dependent, provider: False) -> None:
        # The first phase of ending, in waves: each wave stops the tasks of every fiber that no fiber still serving
        # relies on, and gives them the grace to finish, so what depends on something stops before it does.
        remaining = [service for service in self._services if not service[2].done()]
        while remaining:
            wave = [service for service in remaining
                    if not any(other[0] is not service[0] and relies(other[0], service[0]) for other in remaining)]
            # A cycle of reliance cannot be ordered, so it is stopped all at once.
            wave = wave or remaining
            for _, stopping, _ in wave:
                stopping.set()
            tasks = {task for _, _, task in wave if not task.done()}
            if tasks:
                _, late = await asyncio.wait(tasks, timeout=self.grace)
                for task in late:
                    task.cancel()
                if late:
                    await asyncio.wait(late)
            remaining = [service for service in remaining if service not in wave and not service[2].done()]

    def contain(self, fiber: Fiber, handler: Callable[[Exception, str], None]) -> Inverse:
        self._containers[fiber] = handler

        def release() -> None:
            if self._containers.get(fiber) is handler:
                del self._containers[fiber]

        return release

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

    def _signal(self) -> None:
        changed, self.changed = self.changed, asyncio.Event()
        changed.set()

    def serve(self, ctx: Context, label: str, work: Work, /, *, after_start: bool = True) -> Effect:
        fiber = ctx.fiber

        def start() -> Inverse:
            stopping = asyncio.Event()
            release = self.hold(label)

            async def running() -> None:
                try:
                    if after_start:
                        started = asyncio.ensure_future(self.started.wait())
                        stopped = asyncio.ensure_future(stopping.wait())
                        try:
                            await asyncio.wait({started, stopped}, return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            started.cancel()
                            stopped.cancel()
                        if stopping.is_set():
                            return
                    await work(stopping)
                except asyncio.CancelledError:
                    pass  # cancelled after its grace ran out; the stop that did so has reported nothing more to say
                except Exception as error:  # noqa: BLE001 - reported where the work belongs; cancellation passes
                    self.fail(error, label, fiber)
                finally:
                    release()

            task = asyncio.get_running_loop().create_task(running(), name=label)
            withdraw = self._service(fiber, stopping, task)
            task.add_done_callback(lambda _: withdraw())

            async def stop() -> None:
                stopping.set()
                done, _ = await asyncio.wait({task}, timeout=self.grace)
                if not done:
                    task.cancel()
                    await asyncio.wait({task})

            return stop

        return ctx.effect(start, f"serve {label}")

def application(run: ApplicationRun) -> Component:
    def apply(ctx: Context) -> None:
        ctx.provide(APPLICATION, Application(when_started=run.when_started, hold=run.hold, end=run.end, fail=run.fail,
                                             contain=run.contain, serve=run.serve))
    return Component("application", apply=apply, provides=(APPLICATION,))

APPLICATION_ENTRY = "application"

def with_application(entries: Sequence[Entry], run: ApplicationRun) -> list[Entry]:
    # The entries an application runs: the one providing APPLICATION first, then the composition's own.
    return [Entry(APPLICATION_ENTRY, lambda: application(run)), *entries]

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

async def _until_done(runtime: Runtime, app: ApplicationRun) -> None:
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
    app = ApplicationRun(grace=grace)

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
