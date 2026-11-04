from cordis import Component, Context

from .contracts import SESSION_STATE, SessionState


def session_state() -> Component:
    # A fresh state each time it loads, once per session, for tools to keep what they learn between executions.
    def apply(ctx: Context) -> None:
        ctx.provide(SESSION_STATE, SessionState())
    return Component("session state", apply=apply, provides=(SESSION_STATE,))
