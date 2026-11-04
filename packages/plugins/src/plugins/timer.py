import asyncio
from typing import Callable

from cordis import Component, Context, Entries, Inverse, Key


class Timer:
    # The value bound at TIMER: callbacks run later on the running loop. Each scheduling returns the inverse that
    # cancels it, so a timer set as an effect never fires after its fiber has unloaded.
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

    def cancel_all(self) -> None:
        for handle in list(self._handles):
            handle.cancel()

    def __len__(self) -> int:
        # How many callbacks are scheduled and not yet run or cancelled.
        return len(self._handles)

TIMER: Key[Timer] = Key("timer")

def timer() -> Component:
    def apply(ctx: Context) -> Inverse:
        timers = Timer()
        ctx.provide(TIMER, timers)

        # Whatever a consumer scheduled outside an effect is cancelled with the timer itself.
        return timers.cancel_all
    return Component("timer", apply=apply, provides=(TIMER,))
