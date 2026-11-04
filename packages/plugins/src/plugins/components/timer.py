import asyncio
from typing import Callable

from cordis import Component, Context, Entries, Inverse

from ..contracts import TIMER, Timer


class _LoopTimer:
    # Schedules on the running loop, keeping each handle as an entry of its own until it runs or is cancelled.
    __slots__ = ("_handles",)

    def __init__(self) -> None:
        self._handles: Entries[asyncio.TimerHandle] = Entries()

    def after(self, delay: float, callback: Callable[[], None]) -> Inverse:
        loop = asyncio.get_running_loop()
        remove: list[Inverse] = []

        def fire() -> None:
            remove[0]()
            callback()

        handle = loop.call_later(delay, fire)
        remove.append(self._handles.add(handle))

        def cancel() -> None:
            handle.cancel()
            remove[0]()

        return cancel

    def every(self, interval: float, callback: Callable[[], None]) -> Inverse:
        current: list[Inverse] = []

        def tick() -> None:
            current[0] = self.after(interval, tick)
            callback()

        current.append(self.after(interval, tick))
        return lambda: current[0]()

    def pending(self) -> int:
        return len(self._handles)

    def cancel_all(self) -> None:
        for handle in list(self._handles):
            handle.cancel()

def timer() -> Component:
    def apply(ctx: Context) -> Inverse:
        timers = _LoopTimer()
        ctx.provide(TIMER, Timer(after=timers.after, every=timers.every, pending=timers.pending))
        # Whatever a consumer scheduled outside an effect is cancelled with the timer itself.
        return timers.cancel_all
    return Component("timer", apply=apply, provides=(TIMER,))
