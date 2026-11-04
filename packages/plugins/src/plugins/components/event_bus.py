from cordis import Component, Context

from ..contracts import EVENTS, Events


def event_bus() -> Component:
    # Delivers each event to the handlers subscribed to its topic. A subscription is an effect of the subscriber, so
    # a subscriber that unloads stops receiving, and one that loads later starts; there is nothing to declare up front.
    def apply(ctx: Context) -> None:
        ctx.provide(EVENTS, Events())
    return Component("event bus", apply=apply, provides=(EVENTS,))
