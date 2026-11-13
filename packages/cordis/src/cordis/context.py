from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping, TypeVar, cast

from .component import Apply, Component
from .effect import Activation, Inverse
from .errors import InactiveAccess, ProvisionError, UndeclaredAccess
from .key import Key, Metadata, Realm, RealmId
from .work import Work, spawn

if TYPE_CHECKING:
    from .runtime import Effect, ErrorHandler, Fiber, Runtime

T = TypeVar("T")

class Context:
    # Γ∞ for one fiber: the only way a component touches anything shared (the context paradigm, Section 3.3). It
    # carries the fiber that owns its effects, and what it inherits that changes how keys resolve beneath it: the
    # realm table ρ of isolation, and the layers whose merge is the metadata table ι of interception. Deriving a context
    # never writes the shared table, so it needs no inverse: discarding the derived context recovers (the derived
    # realization, Definition 23).
    __slots__ = ("_intercepts", "_realms", "fiber", "runtime")

    def __init__(
        self,
        runtime: "Runtime",
        fiber: "Fiber",
        realms: Mapping[Key[Any], RealmId],
        intercepts: tuple[Mapping[Key[Any], Metadata], ...],
    ) -> None:
        self.runtime = runtime
        self.fiber = fiber
        self._realms = realms
        # Outermost first. Metadata is consulted when a binding is accessed, never stored on a fiber, so a layer held
        # by reference can change in place and the next access sees it, with nothing reloaded.
        self._intercepts = intercepts

    def derive(self, fiber: "Fiber") -> "Context":
        # The context a child fiber runs in: its own effects, the realms and metadata of where it was instantiated.
        return Context(self.runtime, fiber, self._realms, self._intercepts)

    # --- reading coeffects ---------------------------------------------------------------------------------------

    def realm(self, key: Key[Any]) -> RealmId:
        return self._realms.get(key, key)

    def metadata(self, key: Key[Any]) -> Metadata:
        # The context-carried metadata ι(k): each enclosing layer merged onto the ones outside it, so the innermost
        # takes priority; empty unless some enclosing context intercepted the key.
        merged: Metadata = {}
        for layer in self._intercepts:
            if key in layer:
                merged = key.merge(merged, layer[key])
        return merged

    def get(self, key: Key[T]) -> T:
        # Algorithm 6: resolves against the committed view of the nearest fiber up the chain that declared the key,
        # never against the live table, so a dependency stays readable through the teardown its departure caused.
        fiber = self.fiber
        while True:
            committed = fiber.committed
            if committed is not None and key in committed:
                binding = committed[key].table.get(fiber.realms[key])
                if binding is None:
                    raise InactiveAccess(f"{committed[key].name} no longer holds {key.name}")
                declared = fiber.component.metadata.get(key, {})
                return cast(T, binding.resolve(key.merge(declared, self.metadata(key))))
            if key in fiber.component.requires:
                raise InactiveAccess(f"{fiber.name} requires {key.name} but is not activated against a provider of it")
            if fiber.parent is None:
                raise UndeclaredAccess(f"{self.fiber.name} reads {key.name} without requiring it")
            fiber = fiber.parent

    def find(self, key: Key[T]) -> T | None:
        # A probe for an optional key: what an active provider binds in this context's realm, or None. It never
        # raises and commits to nothing, so nothing reactivates when the answer changes; require the key, or use
        # inject, to follow it.
        provider = self.runtime.provider(self.realm(key))
        if provider is None:
            return None
        return cast(T, provider.table[self.realm(key)].resolve(self.metadata(key)))

    # --- effects -------------------------------------------------------------------------------------------------

    def effect(self, callback: Callable[[], Activation], label: str = "effect") -> "Effect":
        # Performs a revertible effect owned by this context's fiber: callback returns its inverse, or yields one
        # per step, and the fiber's unloading applies them last in first out.
        return self.runtime.effect(self.fiber, callback, label)

    def provide(self, key: Key[T], value: T) -> "Effect":
        # set(k, v) of Definition 20, an effect whose inverse withdraws the binding.
        return self._provide(key, value, None)

    def provide_by(self, key: Key[T], factory: Callable[[Metadata], T]) -> "Effect":
        # Provides a function of interception metadata (Definition 26): each access is served by factory applied to
        # the metadata the accessing component declared, overridden by what its enclosing contexts carry.
        return self._provide(key, None, factory)

    def _provide(self, key: Key[Any], value: object, factory: Callable[[Metadata], object] | None) -> "Effect":
        from .runtime import Binding
        owner = self.fiber
        if key not in owner.component.provides:
            raise ProvisionError(f"{owner.name} provides {key.name} without declaring it")
        realm = owner.realms[key]
        if realm in owner.table:
            raise ProvisionError(f"{owner.name} already provides {key.name}; a key cannot be provided twice")
        runtime = self.runtime

        def install() -> Inverse:
            owner.table[realm] = Binding(value, factory)
            runtime.notify(realm)

            def withdraw() -> None:
                owner.table.pop(realm, None)
                runtime.notify(realm)

            return withdraw

        return self.effect(install, f"provide {key.name}")

    def hold(self, label: str = "hold") -> "Effect":
        # Keeps the runtime from being done while this is in effect: for whatever this fiber is still doing that is not
        # a task, such as a timer yet to fire or a server run by a library on a thread of its own. Unloading the fiber
        # lets go, as does disposing the effect.
        return self.effect(lambda: self.runtime.hold(self.fiber, label), f"hold {label}")

    def spawn(self, work: Work, label: str = "work", *, hold: bool = True, grace: float | None = None) -> "Effect":
        # Runs work as a task owned by this context's fiber: an effect whose inverse tells the work to stop, through
        # the event it is given, and waits for it, cancelling it once its grace (the runtime's, unless given) runs out.
        # While it runs it holds the runtime, unless hold is False, which is for work that runs beside what the
        # composition is for and should not keep it going by itself. Work that raises fails this fiber's work alone:
        # it is reported where the fiber's failures are.
        return spawn(self, work, label, hold=hold, grace=grace)

    def contain(self, handler: "ErrorHandler", fiber: "Fiber | None" = None) -> "Effect":
        # Gives the failures of fiber (this context's own unless given), and of everything instantiated beneath it,
        # to handler instead of whatever contains this fiber, while this is in effect.
        contained = fiber if fiber is not None else self.fiber
        return self.effect(lambda: self.runtime.contain(contained, handler), f"contain {contained.name}")

    def use(self, component: Component) -> "Fiber":
        # Instantiates component beneath this context's fiber (Definition 52). The instantiation is one of this
        # fiber's effects, so unloading this fiber retires the child.
        return self.runtime.instantiate(self, component)

    def inject(self, keys: Collection[Key[Any]], apply: Apply, name: str | None = None) -> "Fiber":
        # A child that requires keys and runs apply while they are provided: an optional dependency as a sub-fiber,
        # adding behaviour while the keys exist and withdrawing it when they leave.
        return self.use(Component(name or f"{self.fiber.name}/inject", apply=apply, requires=tuple(keys)))

    # --- derived contexts ----------------------------------------------------------------------------------------

    def isolate(self, key: Key[Any], realm: Realm | None = None) -> "Context":
        # isolate(k, r) of Definition 25: fibers instantiated beneath the derived context resolve key in realm, a
        # fresh one unless given, so the same key binds independently there.
        realms = {**self._realms, key: realm if realm is not None else Realm(self.fiber.name)}
        return Context(self.runtime, self.fiber, realms, self._intercepts)

    def intercept(self, key: Key[Any], metadata: Metadata) -> "Context":
        # intercept(k, ν) of Definition 27: merges metadata onto what this context carries at key, taking priority.
        return self.layered({key: metadata})

    def layered(self, layer: Mapping[Key[Any], Metadata]) -> "Context":
        # Intercepts every key layer names at once. The layer is held by reference: whoever owns it may change it, and
        # every access beneath the derived context reads it as it then stands (how a loader updates interception
        # without reloading anything, Section 5.2.1).
        return Context(self.runtime, self.fiber, self._realms, (*self._intercepts, layer))

    def __repr__(self) -> str:
        return f"Context({self.fiber.name!r})"
