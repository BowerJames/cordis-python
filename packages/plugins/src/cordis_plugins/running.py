import asyncio
import signal
from contextlib import contextmanager, suppress
from typing import Iterator, Sequence

from cordis import Fiber, Runtime

# What an entrypoint runs a composition with, beside the runtime's own open, settled and done: what to do about
# failures, and about being asked to end from outside. Neither is a component: both belong to whoever runs the
# composition, and act only by asking the runtime to stop, which makes done return; closing the runtime, by leaving
# its block, then unloads everything, dependents first.

class StopOnFailure:
    # An error handler for a runtime: keeps every failure it reports, with the fiber it happened at, and asks the
    # runtime to stop at the first. A failure a fiber contained never reaches it, and stops nothing.
    def __init__(self) -> None:
        self.failures: list[tuple[str, Exception]] = []

    def __call__(self, fiber: Fiber, error: Exception) -> None:
        error.add_note(f"in {fiber.name}")
        self.failures.append((fiber.name, error))
        fiber.runtime.stop()

    def of[E: Exception](self, kind: type[E]) -> list[E]:
        return [error for _, error in self.failures if isinstance(error, kind)]

    def status(self, given: int | None = None) -> int:
        # The status to exit with: the one given, such as what a component provided at EXIT_STATUS, or 0; and never 0
        # when something failed.
        status = given if given is not None else 0
        return 1 if self.failures and status == 0 else status

@contextmanager
def stop_on_signals(runtime: Runtime, signals: Sequence[signal.Signals] = (signal.SIGINT, signal.SIGTERM)) -> Iterator[None]:
    # While this is entered, a first SIGINT or SIGTERM asks the runtime to stop, so the program ends as it would by
    # itself, everything unloaded in order. Left before the runtime is closed, so a second signal during the unloading
    # ends the process as signals ordinarily do.
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for number in signals:
        # Not every loop or thread can take signal handlers; where one cannot, the signal keeps its usual meaning.
        with suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(number, runtime.stop)
            installed.append(number)
    try:
        yield
    finally:
        for number in installed:
            loop.remove_signal_handler(number)
