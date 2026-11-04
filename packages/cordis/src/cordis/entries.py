from typing import Generic, Iterator, TypeVar

from .effect import Inverse

T = TypeVar("T")

class Entries(Generic[T]):
    # A table that is a commutative coeffect by construction (Section 3.4.2): each addition draws an entry of its own
    # and its inverse removes that entry and no other, so two additions, even of equal values, can be withdrawn in
    # either order while the other stands. A provider whose key holds registrations keeps them in one of these.
    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[object, T] = {}

    def add(self, value: T) -> Inverse:
        ticket = object()
        self._entries[ticket] = value

        def remove() -> None:
            self._entries.pop(ticket, None)

        return remove

    def __iter__(self) -> Iterator[T]:
        # A snapshot, so an entry added or removed while iterating takes effect from the next iteration.
        return iter(tuple(self._entries.values()))

    def __len__(self) -> int:
        return len(self._entries)
