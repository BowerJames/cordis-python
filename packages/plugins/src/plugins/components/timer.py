from cordis import Component, Context, Inverse

from ..contracts import TIMER, Timer


def timer() -> Component:
    def apply(ctx: Context) -> Inverse:
        timers = Timer()
        ctx.provide(TIMER, timers)
        # Whatever a consumer scheduled outside an effect is cancelled with the timer itself.
        return timers.cancel_all
    return Component("timer", apply=apply, provides=(TIMER,))
