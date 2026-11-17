import asyncio
from typing import Awaitable, Callable

from cordis import Context, Undo

# Work that runs for a while, such as a server accepting connections or an agent answering a session: told to stop by
# the event it is given, and expected to return soon after.
type Work = Callable[[asyncio.Event], Awaitable[None]]

def background(ctx: Context, label: str, work: Work, *, grace: float = 5.0) -> Undo:
    # Starts work as a task belonging to ctx's fiber, and gives back the inverse that stops it, for the fiber to yield:
    # a resource acquired in one step and released by its inverse (Definition 8), with nothing kept beside it. Applying
    # the inverse sets the work's event, gives it grace seconds to return, and cancels it after that. Work that raises
    # fails ctx's fiber (Context.fail), which leaves and rests failed, as one whose activation raised does.
    stopping = asyncio.Event()

    async def running() -> None:
        try:
            await work(stopping)
        except asyncio.CancelledError:
            pass  # cancelled once its grace ran out: the stop that did so has nothing more to report
        except Exception as error:  # noqa: BLE001 - a failure belongs to the fiber the work belongs to
            ctx.fail(error)

    task = asyncio.get_running_loop().create_task(running(), name=label)

    async def stop() -> None:
        stopping.set()
        done, _ = await asyncio.wait({task}, timeout=grace)
        if not done:
            task.cancel()
            await asyncio.wait({task})

    return Undo(stop, label)
