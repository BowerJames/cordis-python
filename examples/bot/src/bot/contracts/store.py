from dataclasses import dataclass
from typing import Callable

from cordis import Key


@dataclass(slots=True, frozen=True)
class Store:
    # A counter per name, kept wherever the provider keeps it: increment counts one more, and gives the count.
    increment: Callable[[str], int]

STORE: Key[Store] = Key("store")
