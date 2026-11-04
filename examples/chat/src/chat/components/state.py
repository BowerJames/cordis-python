from typing import Any

from cordis import Component, Context

from ..contracts import SESSION_STATE, SessionState, StateKey


class _Values:
    __slots__ = ("_values",)

    def __init__(self) -> None:
        self._values: dict[StateKey[Any], Any] = {}

    def get[V](self, key: StateKey[V], /) -> V:
        if key not in self._values:
            self._values[key] = key.initial()
        value: V = self._values[key]
        return value

    def set[V](self, key: StateKey[V], value: V, /) -> None:
        self._values[key] = value

def session_state() -> Component:
    # A fresh state each time it loads, once per session, for tools to keep what they learn between executions.
    def apply(ctx: Context) -> None:
        values = _Values()
        ctx.provide(SESSION_STATE, SessionState(get=values.get, set=values.set))
    return Component("session state", apply=apply, provides=(SESSION_STATE,))
