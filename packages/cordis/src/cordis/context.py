from typing import TYPE_CHECKING, Any, Awaitable, Callable, Collection, Mapping, TypeVar, cast, overload

from .component import Component
from .effect import Activation, Inverse, Undo
from .errors import InactiveAccess, ProvisionError, UndeclaredAccess
from .key import Key, Metadata, Realm, RealmId

if TYPE_CHECKING:
    from .runtime import Fiber, Runtime

T = TypeVar("T")
C = TypeVar("C")

# Stands for a configuration not given to use, which then takes the defaults of the component's schema.
_DEFAULTS: Any = object()

class Context:
    # Γ∞ for one fiber: the only way a component touches anything shared (the context paradigm, Section 3.3), and so
    # offering nothing but the operations the calculus models: reading what the component declared, effects that give
    # their inverses back, instantiation, and the derived contexts of isolation and interception. The runtime and the
    # fiber it carries are the kernel's, reached by an orchestrator through cordis.orchestration. It
    # carries the fiber that owns its effects, and what it inherits that changes how keys resolve beneath it: the
    # realm table ρ of isolation, and the layers whose merge is the metadata table ι of interception. Deriving a context
    # never writes the shared table, so it needs no inverse: discarding the derived context recovers (the derived
    # realization, Definition 23).
    __slots__ = ("_fiber", "_intercepts", "_realms", "_runtime")

    def __init__(
        self,
        runtime: "Runtime",
        fiber: "Fiber",
        realms: Mapping[Key[Any], RealmId],
        intercepts: tuple[Mapping[Key[Any], Metadata], ...],
    ) -> None:
        self._runtime = runtime
        self._fiber = fiber
        self._realms = realms
        # Outermost first. Metadata is consulted when a binding is accessed, never stored on a fiber, so a layer held
        # by reference can change in place and the next access sees it, with nothing reloaded.
        self._intercepts = intercepts

    def _derive(self, fiber: "Fiber") -> "Context":
        # The context a child fiber runs in: its own effects, the realms and metadata of where it was instantiated.
        return Context(self._runtime, fiber, self._realms, self._intercepts)

    # --- reading coeffects ---------------------------------------------------------------------------------------

    def _realm(self, key: Key[Any]) -> RealmId:
        return self._realms.get(key, key)

    def _metadata(self, key: Key[Any]) -> Metadata:
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
        fiber = self._fiber
        while True:
            committed = fiber.committed
            if committed is not None and key in committed:
                binding = committed[key].table.get(fiber.realms[key])
                if binding is None:
                    raise InactiveAccess(f"{committed[key].name} no longer holds {key.name}")
                declared = fiber.component.metadata.get(key, {})
                return cast(T, binding.resolve(key.merge(declared, self._metadata(key))))
            if key in fiber.component.requires:
                raise InactiveAccess(f"{fiber.name} requires {key.name} but is not activated against a provider of it")
            if fiber.parent is None:
                raise UndeclaredAccess(f"{self._fiber.name} reads {key.name} without requiring it")
            fiber = fiber.parent

    # --- effects -------------------------------------------------------------------------------------------------

    # Whatever a context does for a component, it gives the inverse back and holds nothing: the component yields each
    # inverse from its effect function, so its accumulator holds them in the order the function says, and every yield
    # is a seam the activation can stop at. Every Undo made during a step is owed by the fiber, whatever made it, and
    # one still unyielded at the next seam fails the activation.

    def adopt(self, inverse: Inverse, label: str = "an effect") -> Undo:
        # Takes an inverse onto this fiber's accumulator at once, where there is nothing to yield it to: an effect
        # performed once the fiber is active, or by an orchestrator working beneath this context. Applying the undo
        # it gives back reverts that one effect early.
        return self._runtime.adopt(self._fiber, inverse, label)

    def fail(self, error: Exception) -> None:
        # Reports that something this fiber started has failed, after the step that started it: work it runs in the
        # background, say. The fiber takes the route a raising iteration takes (Section 4.4): it stops providing,
        # unloads, and rests failed with the error, not retried until revised. A fiber already leaving only reports it.
        self._runtime.fail(self._fiber, error)

    def provide(self, key: Key[T], value: T) -> Undo:
        # set(k, v) of Definition 20: binds value at key, and returns the inverse that withdraws the binding.
        return self._provide(key, value, None)

    def provide_by(self, key: Key[T], factory: Callable[[Metadata], T]) -> Undo:
        # Provides a function of interception metadata (Definition 26): each access is served by factory applied to
        # the metadata the accessing component declared, overridden by what its enclosing contexts carry.
        return self._provide(key, None, factory)

    def _provide(self, key: Key[Any], value: object, factory: Callable[[Metadata], object] | None) -> Undo:
        from .runtime import Binding
        owner = self._fiber
        if key not in owner.component.provides:
            raise ProvisionError(f"{owner.name} provides {key.name} without declaring it")
        realm = owner.realms[key]
        if realm in owner.table:
            raise ProvisionError(f"{owner.name} already provides {key.name}; a key cannot be provided twice")
        runtime = self._runtime
        runtime.check_effectful(owner)

        def withdraw() -> None:
            owner.table.pop(realm, None)
            runtime.notify(realm)

        owner.table[realm] = Binding(value, factory)
        undo = Undo(withdraw, f"provide {key.name}")
        # Owed by the step that made it, or else by this fiber: the kernel knows whose binding this is.
        owner.owe(undo)
        runtime.notify(realm)
        return undo

    @overload
    def use(self, component: Component[C], config: C) -> "Fiber": ...

    @overload
    def use(self, component: Component[Any]) -> "Fiber": ...

    def use(self, component: Component[Any], config: Any = _DEFAULTS) -> "Fiber":
        # Instantiates component beneath this context's fiber with config bound into its effect function (Definition
        # 52, Algorithm 4); without one, with the defaults of its schema. The child's O-Retire is the inverse of the
        # instantiation, and it is the fiber's retire: yield it, and unloading this fiber retires the child.
        return self._runtime.instantiate(self, component, component.configure() if config is _DEFAULTS else config)

    def while_provided(self, keys: Collection[Key[Any]], apply: Callable[["Context"], Activation],
                       name: str | None = None) -> "Fiber":
        # A child that requires keys and runs apply while they are provided: how a component does something only while
        # a key it does not itself require is there, the child activating as the key arrives and leaving as it goes.
        # The keys are the child's d, and the parent's own activation never waits on them. Yield its retire, as for use.
        return self.use(Component(name or f"{self._fiber.name} while provided", apply, requires=tuple(keys)))

    def accept(self, handler: Callable[[C], bool | Awaitable[bool]]) -> Undo:
        # Says how this fiber takes a new configuration while it is active (Section 5.2.1: the payload is handed to the
        # component, which decides how to apply it). Fiber.update calls handler with the new configuration; returning
        # True says the fiber now runs as one activated with it would, so nothing reloads, and returning False asks
        # for the revision that is the default. Typically it diffs against the configuration it activated with, and
        # accepts only what it can apply in place. Yield the undo: the handler is withdrawn as the fiber unloads.
        fiber = self._fiber
        self._runtime.check_effectful(fiber)
        undo = fiber.acceptors.add(cast(Callable[[Any], bool | Awaitable[bool]], handler), "accept a configuration")
        fiber.owe(undo)
        return undo

    # --- derived contexts ----------------------------------------------------------------------------------------

    def isolate(self, key: Key[Any], realm: Realm | None = None) -> "Context":
        # isolate(k, r) of Definition 25: fibers instantiated beneath the derived context resolve key in realm, a
        # fresh one unless given, so the same key binds independently there.
        realms = {**self._realms, key: realm if realm is not None else Realm(self._fiber.name)}
        return Context(self._runtime, self._fiber, realms, self._intercepts)

    def intercept(self, key: Key[Any], metadata: Metadata) -> "Context":
        # intercept(k, ν) of Definition 27: merges metadata onto what this context carries at key, taking priority.
        return self._layered({key: metadata})

    def _layered(self, layer: Mapping[Key[Any], Metadata]) -> "Context":
        # Intercepts every key layer names at once. The layer is held by reference: whoever owns it may change it, and
        # every access beneath the derived context reads it as it then stands (how a loader updates interception
        # without reloading anything, Section 5.2.1).
        return Context(self._runtime, self._fiber, self._realms, (*self._intercepts, layer))

    def __repr__(self) -> str:
        return f"Context({self._fiber.name!r})"
