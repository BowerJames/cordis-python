from dataclasses import dataclass
from typing import Callable

from cordis import Inverse


@dataclass(slots=True, frozen=True)
class Catalog[T]:
    # Things on offer, each by a name: providers, agent types, a session's tools. Offering is an entry of its own,
    # withdrawn by the inverse offer returns, so whatever offers comes and goes without disturbing the rest; offering
    # a name already on offer raises ValueError.
    offer: Callable[[T], Inverse]
    # Every name on offer now, in the order they were offered.
    names: Callable[[], tuple[str, ...]]
    # What is on offer by a name, or None.
    get: Callable[[str], T | None]
