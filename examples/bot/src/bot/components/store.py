from cordis import Component, Context

from ..contracts import STORE, Store


class _Memory:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def increment(self, name: str) -> int:
        self._counts[name] = self._counts.get(name, 0) + 1
        return self._counts[name]

def memory_store() -> Component:
    def apply(ctx: Context) -> None:
        memory = _Memory()
        ctx.provide(STORE, Store(increment=memory.increment))
    return Component("memory store", apply=apply, provides=(STORE,))
