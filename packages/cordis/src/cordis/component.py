from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Collection, Generic, Mapping, TypeVar, cast, overload

from .effect import Activation
from .key import Key, Metadata

if TYPE_CHECKING:
    from .context import Context

C = TypeVar("C")

# The effect function e of Definition 48, as Algorithm 4 calls it: against the fiber's own context and the configuration
# the fiber was instantiated with. Everything it does to the world goes through that context: an operation on a declared
# key, a provision of its own, or an instantiation (Definition 56). A generator, sync or async, makes each yield an
# iteration with a seam after it.
type Apply[C] = Callable[["Context", C], Activation]

# What builds a component's configuration from keyword arguments, as an entry gives them: a dataclass is one. Calling it
# is where a configuration is checked, so a bad one fails as it is loaded, on the entry that holds it.
type Schema[C] = Callable[..., C]

@dataclass(slots=True, frozen=True, init=False, eq=False)
class Component(Generic[C]):
    # (d, p, e): what it requires from the environment, what it may provide to it, and the effect function that
    # contributes its effects while it is active. The configuration is the fiber's, not the component's: instantiation
    # binds it into e (Algorithm 4, line 9), so one component serves every configuration, and a running fiber can be
    # handed a new one (Fiber.update) without anything being rebuilt that does not have to be. A component with no
    # configuration has no schema, and its effect function takes the context alone.
    name: str
    apply: Apply[C] | None
    config: Schema[C] | None
    # Each declaration is empty unless given, so a component names only what it does.
    requires: Collection[Key[Any]]
    provides: Collection[Key[Any]]
    # Component-declared interception metadata d(k), merged under whatever the enclosing context carries.
    metadata: Mapping[Key[Any], Metadata]

    @overload
    def __init__(
        self: "Component[None]",
        name: str,
        apply: Callable[["Context"], Activation] | None = None,
        *,
        requires: Collection[Key[Any]] = (),
        provides: Collection[Key[Any]] = (),
        metadata: Mapping[Key[Any], Metadata] | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        name: str,
        apply: Apply[C],
        *,
        config: Schema[C],
        requires: Collection[Key[Any]] = (),
        provides: Collection[Key[Any]] = (),
        metadata: Mapping[Key[Any], Metadata] | None = None,
    ) -> None: ...

    def __init__(
        self,
        name: str,
        apply: Callable[..., Activation] | None = None,
        *,
        config: Schema[C] | None = None,
        requires: Collection[Key[Any]] = (),
        provides: Collection[Key[Any]] = (),
        metadata: Mapping[Key[Any], Metadata] | None = None,
    ) -> None:
        unconfigured = apply
        if config is None and unconfigured is not None:
            # As a function of JavaScript ignores an argument it does not name: the configuration is None, unread.
            def apply(ctx: "Context", _: C) -> Activation:
                assert unconfigured is not None
                return unconfigured(ctx)
        for field, value in (
            ("name", name), ("apply", apply), ("config", config), ("requires", tuple(requires)),
            ("provides", tuple(provides)), ("metadata", dict(metadata or {})),
        ):
            object.__setattr__(self, field, value)
        overlap = set(self.requires) & set(self.provides)
        if overlap:
            names = ", ".join(sorted(key.name for key in overlap))
            raise ValueError(f"{self.name} both requires and provides {names}, so it could never be satisfied")
        undeclared = set(self.metadata) - set(self.requires)
        if undeclared:
            names = ", ".join(sorted(key.name for key in undeclared))
            raise ValueError(f"{self.name} declares metadata for {names}, which it does not require")

    def configure(self, values: Mapping[str, Any] | None = None) -> C:
        # The configuration values describe, built and checked by the schema: what an entry's config becomes. With no
        # values, the schema's defaults; a component with no schema takes no configuration at all.
        if self.config is None:
            if values:
                raise TypeError(f"{self.name} takes no configuration, but was given {', '.join(sorted(values))}")
            return cast(C, None)
        return self.config(**(values or {}))

    def __repr__(self) -> str:
        return f"Component({self.name!r})"
