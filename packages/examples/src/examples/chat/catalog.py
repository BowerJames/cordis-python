from typing import Callable

from cordis import Entries, Inverse

from .contracts import Catalog


class _Offered[T]:
    # Each offer an entry of its own; names are unique among what is on offer at once.
    __slots__ = ("_entries", "_name")

    def __init__(self, name: Callable[[T], str]) -> None:
        self._name = name
        self._entries: Entries[T] = Entries()

    def offer(self, item: T) -> Inverse:
        if self._name(item) in self.names():
            raise ValueError(f"{self._name(item)} is already on offer")
        return self._entries.add(item)

    def names(self) -> tuple[str, ...]:
        return tuple(self._name(item) for item in self._entries)

    def get(self, name: str) -> T | None:
        return next((item for item in self._entries if self._name(item) == name), None)

def catalog[T](name: Callable[[T], str]) -> Catalog[T]:
    # A catalog kept in memory, naming each thing on offer by name.
    offered = _Offered(name)
    return Catalog(offer=offered.offer, names=offered.names, get=offered.get)
