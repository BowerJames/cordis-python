from cordis import Component, Context
from cordis_plugins import EVENTS, STDOUT

from ..contracts import SESSION_CLOSED, SESSION_OPENED, SessionClosed, SessionOpened


def session_log() -> Component:
    # A line on stdout as each session opens and closes, with why it failed when it did. Its subscriptions are its
    # effects, so it hears every session for as long as it is loaded, and nothing once it is not.
    def apply(ctx: Context) -> None:
        async def opened(event: SessionOpened) -> None:
            request = event.request
            ctx.get(STDOUT)(f"session {event.session} opened: {request.provider}/{request.model_name} "
                            f"as {request.agent_type}\n")

        async def closed(event: SessionClosed) -> None:
            if event.failures:
                ctx.get(STDOUT)(f"session {event.session} failed: {'; '.join(event.failures)}\n")
            else:
                ctx.get(STDOUT)(f"session {event.session} closed\n")

        ctx.effect(lambda: ctx.get(EVENTS).subscribe(SESSION_OPENED, opened), "log sessions opening")
        ctx.effect(lambda: ctx.get(EVENTS).subscribe(SESSION_CLOSED, closed), "log sessions closing")

    return Component("session log", apply=apply, requires=(EVENTS, STDOUT))
