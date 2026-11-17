from cordis import Component, Context, Inverse
from cordis_plugins import STDOUT

from ..contracts import SESSION_LOG, SessionClosed, SessionLog, SessionOpened


def session_log() -> Component:
    # A line on stdout as each session opens and closes, with why it failed when it did. Sessions depend on it, so it
    # stays until the last of them has closed.
    def apply(ctx: Context) -> Inverse:
        def opened(event: SessionOpened) -> None:
            request = event.request
            ctx.get(STDOUT)(f"session {event.session} opened: {request.provider}/{request.model_name} "
                            f"as {request.agent_type}\n")

        def closed(event: SessionClosed) -> None:
            if event.failures:
                ctx.get(STDOUT)(f"session {event.session} failed: {'; '.join(event.failures)}\n")
            else:
                ctx.get(STDOUT)(f"session {event.session} closed\n")

        return ctx.provide(SESSION_LOG, SessionLog(opened=opened, closed=closed))

    return Component("session log", apply=apply, requires=(STDOUT,), provides=(SESSION_LOG,))
