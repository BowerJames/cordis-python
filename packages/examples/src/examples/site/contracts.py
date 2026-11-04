from dataclasses import dataclass
from typing import Awaitable, Callable

from cordis import Key


@dataclass(slots=True)
class Item:
    id: int
    name: str

@dataclass(slots=True, frozen=True)
class Items:
    # Where items are kept: add numbers each new item from 1, in the order they are added; get is None for a number
    # never given out.
    add: Callable[[str], Awaitable[Item]]
    get: Callable[[int], Awaitable[Item | None]]

ITEMS: Key[Items] = Key("items")
