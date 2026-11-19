import asyncio
from typing import Callable, Iterator

from cordis import Component, Context, Inverse, Undo

from ..contracts import TIMER, Timer


class _LoopTimer:
    # Schedules on the running loop, keeping each handle under a ticket of its own until it runs or is cancelled.
    __slots__ = ("_handles",)

    def __init__(self) -> None:
        self._handles: dict[object, asyncio.TimerHandle] = {}

    def _schedule(self, delay: float, callback: Callable[[], None]) -> Callable[[], None]:
        ticket = object()

        def fire() -> None:
            self._handles.pop(ticket, None)
            callback()

        self._handles[ticket] = asyncio.get_running_loop().call_later(delay, fire)

        def cancel() -> None:
            handle = self._handles.pop(ticket, None)
            if handle is not None:
                handle.cancel()

        return cancel

    def after(self, delay: float, callback: Callable[[], None]) -> Undo:
        return Undo(self._schedule(delay, callback), f"a timer after {delay}s")

    def every(self, interval: float, callback: Callable[[], None]) -> Undo:
        current: list[Callable[[], None]] = []

        def tick() -> None:
            current[0] = self._schedule(interval, tick)
            callback()

        current.append(self._schedule(interval, tick))
        return Undo(lambda: current[0](), f"a timer every {interval}s")

    def pending(self) -> int:
        return len(self._handles)

    def cancel_all(self) -> None:
        for handle in self._handles.values():
            handle.cancel()
        self._handles.clear()

def _timer(ctx: Context) -> Iterator[Inverse]:
    timers = _LoopTimer()
    # Whatever is still scheduled when the timer itself unloads is cancelled with it.
    yield timers.cancel_all
    yield ctx.provide(TIMER, Timer(after=timers.after, every=timers.every, pending=timers.pending))

timer = Component("timer", _timer, provides=(TIMER,))
