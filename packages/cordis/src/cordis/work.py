import asyncio
from typing import TYPE_CHECKING, Awaitable, Callable

from .effect import Inverse

if TYPE_CHECKING:
    from .context import Context
    from .runtime import Effect

# Work that runs for a while, such as a server accepting connections or one request being made: told to stop by the
# event it is given, and expected to return soon after.
type Work = Callable[[asyncio.Event], Awaitable[None]]

def spawn(ctx: "Context", work: Work, label: str = "work", *, hold: bool = True, grace: float | None = None) -> "Effect":
    # A task is something acquired (Section 6.1): starting it is an effect of the fiber, and its inverse ends it. What
    # the work did while it ran is emission, which no inverse takes back. Built from the context's own primitives, an
    # effect and a hold, so the runtime knows of holds and never of tasks.
    fiber, runtime = ctx.fiber, ctx.runtime

    def start() -> Inverse:
        stopping = asyncio.Event()
        release = runtime.hold(fiber, label) if hold else None

        async def running() -> None:
            try:
                await work(stopping)
            except asyncio.CancelledError:
                pass  # cancelled once its grace ran out; whoever cancelled it has nothing more to be told
            except Exception as error:  # noqa: BLE001 - reported where the fiber's failures are; cancellation passes
                runtime.report(fiber, error)
            finally:
                # However the work ended, by itself or told to, it holds nothing any more.
                if release is not None:
                    release()

        # Started lazily whatever the loop's task factory, as the runtime starts its own: the inverse is in the fiber's
        # accumulator before any of the work runs.
        task = asyncio.Task(running(), loop=asyncio.get_running_loop(), eager_start=False, name=label)

        async def stop() -> None:
            stopping.set()
            if task.done() or task is asyncio.current_task():
                # Work that ends its own effect is not waited for by itself: it has been told, and returns.
                return
            # Waited for, so the inverses applied after this one meet a state the work has left: a socket is closed
            # only once whatever was serving on it has returned.
            waited = runtime.grace if grace is None else grace
            finished, _ = await asyncio.wait({task}, timeout=waited)
            if not finished:
                task.cancel()
                await asyncio.wait({task})

        return stop

    return ctx.effect(start, f"spawn {label}")
