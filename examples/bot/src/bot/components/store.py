from cordis import Component, Context, Inverse

from ..contracts import STORE, Store


class _Memory:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def increment(self, name: str) -> int:
        self._counts[name] = self._counts.get(name, 0) + 1
        return self._counts[name]

def _memory_store(ctx: Context) -> Inverse:
    memory = _Memory()
    return ctx.provide(STORE, Store(increment=memory.increment))

memory_store = Component("memory store", _memory_store, provides=(STORE,))
