import asyncio
from typing import Awaitable, Callable

from cordis import Context, Effect, Entries, Fiber, Inverse, Key

# An application is a composition that runs until it is done: until something ends it, until a failure ends it, or
# until nothing holds it open any more. Cordis itself only loads and unloads; this is the run around that, kept by the
# runner (plugins.components.application) and offered to the components through APPLICATION.
#
# Promises:
#   started is set once everything the composition first loaded has settled, and when_started callbacks run then, in
#   the order they were registered; a callback registered after that runs at once.
#   hold keeps the application running until its inverse is applied; once started and settled, an application held by
#   nothing ends by itself, so work that should keep it running holds it (serve does so for a task).
#   end asks the application to end with a status; the first status asked for wins. fail records a failure, which ends
#   the application with status 1 unless a containing handler takes it.
#   contain gives a fiber's failures, and those of everything it instantiates, to a handler instead: they fail only
#   what was contained, such as one session, and never the application.
#   When it ends, it does so in two phases: first every served task is told to stop and given its grace, with every
#   fiber still loaded, so work that is finishing (a server draining its connections, a session closing) can still use
#   anything; a task whose fiber is relied upon is stopped only once the tasks of the fibers relying on it have, so a
#   registration with a server ends while the server still accepts. Then the runtime is closed, and every fiber
#   unloaded, dependents before what they depend on.

class Application:
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

    def service(self, fiber: Fiber, stopping: asyncio.Event, task: asyncio.Task[None]) -> Inverse:
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

APPLICATION: Key[Application] = Key("application")

# Work that runs for a while, such as a server accepting connections: told to stop by the event it is given, and
# expected to return soon after.
type Work = Callable[[asyncio.Event], Awaitable[None]]

def serve(ctx: Context, label: str, work: Work, *, after_start: bool = True) -> Effect:
    # Runs work as a task owned by ctx's fiber, holding the application open while it runs. It is an effect, so
    # unloading the fiber stops the work: its event is set, it is given the application's grace to finish, and it is
    # cancelled after that. Work that raises fails the application, or whatever contains ctx's fiber. ctx's component
    # must require APPLICATION. Unless told otherwise, the work waits for the application to start before it runs.
    fiber = ctx.fiber

    def start() -> Inverse:
        app = ctx.get(APPLICATION)
        stopping = asyncio.Event()
        release = app.hold(label)

        async def running() -> None:
            try:
                if after_start:
                    started = asyncio.ensure_future(app.started.wait())
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
                app.fail(error, label, fiber)
            finally:
                release()

        task = asyncio.get_running_loop().create_task(running(), name=label)
        withdraw = app.service(fiber, stopping, task)
        task.add_done_callback(lambda _: withdraw())

        async def stop() -> None:
            stopping.set()
            done, _ = await asyncio.wait({task}, timeout=app.grace)
            if not done:
                task.cancel()
                await asyncio.wait({task})

        return stop

    return ctx.effect(start, f"serve {label}")
