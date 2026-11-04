from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping

from .effect import Activation
from .key import Key, Metadata

if TYPE_CHECKING:
    from .context import Context

# The effect function e of Definition 48. It runs against the fiber's own context, and everything it does to the world
# goes through that context: an operation on a declared key, a provision of its own, or an instantiation (Definition
# 56). A generator, sync or async, makes each yield an iteration with a seam after it.
type Apply = Callable[["Context"], Activation]

@dataclass(slots=True, frozen=True)
class Component:
    # (d, p, e): what it requires from the environment, what it may provide to it, and the effect function that
    # contributes its effects while it is active. Configuration is bound into apply, by the factory that builds it,
    # so one component given two configurations is two components.
    name: str
    apply: Apply | None = None
    # Each declaration is empty unless given, so a component names only what it does.
    requires: Collection[Key[Any]] = ()
    provides: Collection[Key[Any]] = ()
    # Component-declared interception metadata d(k), merged under whatever the enclosing context carries.
    metadata: Mapping[Key[Any], Metadata] = field(default_factory=dict)

    def __post_init__(self) -> None:
        overlap = set(self.requires) & set(self.provides)
        if overlap:
            names = ", ".join(sorted(key.name for key in overlap))
            raise ValueError(f"{self.name} both requires and provides {names}, so it could never be satisfied")
        undeclared = set(self.metadata) - set(self.requires)
        if undeclared:
            names = ", ".join(sorted(key.name for key in undeclared))
            raise ValueError(f"{self.name} declares metadata for {names}, which it does not require")
