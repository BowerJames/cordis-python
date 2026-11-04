import asyncio
import itertools
import logging
from contextlib import asynccontextmanager
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Coroutine, Generator, Mapping

from .component import Component
from .effect import Accumulator, Activation, Inverse, execute, execute_now, needs_awaiting
from .errors import InactiveEffect, InsertionError
from .key import Key, Metadata, RealmId

if TYPE_CHECKING:
    from .context import Context

logger = logging.getLogger("cordis")

class FiberState(Enum):
    # The lifecycle θ of Definition 49. A fiber rests at INACTIVE or ACTIVE; LOADING (the paper's Reloading) and
    # UNLOADING are the transitions between them, each spread over an interval. FAILED is INACTIVE holding the error
    # an iteration raised (Section 4.4): it has installed nothing, and is not retried until it is revised.
    INACTIVE = "inactive"
    LOADING = "loading"
    ACTIVE = "active"
    UNLOADING = "unloading"
    FAILED = "failed"

# A fiber is installed while its lifecycle state carries an accumulator and a committed view (Equation 44).
INSTALLED = frozenset({FiberState.LOADING, FiberState.ACTIVE, FiberState.UNLOADING})
SETTLED = frozenset({FiberState.INACTIVE, FiberState.FAILED})

type ErrorHandler = Callable[["Fiber", Exception], None]
# A committed or target view: each declared key mapped to the fiber providing it.
type View = Mapping[Key[Any], "Fiber"]

def log_error(fiber: "Fiber", error: Exception) -> None:
    logger.error("%s: %s", fiber.name, error, exc_info=error)

class Binding:
    # One entry of a fiber's own table σₙ: a value, or a provider function of interception metadata (Definition 26).
    __slots__ = ("factory", "value")

    def __init__(self, value: object = None, factory: Callable[[Metadata], object] | None = None) -> None:
        self.value = value
        self.factory = factory

    def resolve(self, metadata: Metadata) -> object:
        return self.value if self.factory is None else self.factory(metadata)

class Effect:
    # One tracked effect (Algorithm 1): its inverses accumulate here, and its own disposal, composed onto the owning
    # fiber's accumulator, reverts them all. Disposal happens at most once, and halts an iteration still under way.
    __slots__ = ("_inverse", "accumulator", "armed", "error", "label", "owner", "task")

    def __init__(self, owner: "Fiber", label: str) -> None:
        self.owner = owner
        self.label = label
        self.armed = True
        self.error: Exception | None = None
        self.accumulator = Accumulator()
        self.task: asyncio.Task[None] | None = None
        # Kept as one object, so the owner can discard exactly it when the effect is disposed early.
        self._inverse: Inverse = self.dispose

    async def dispose(self) -> None:
        if not self.armed:
            return
        self.armed = False
        self.owner.accumulator.discard(self._inverse)
        if self.task is not None and self.task is not asyncio.current_task():
            await asyncio.wait([self.task])
        for error in await self.accumulator.recover():
            self.owner.runtime.report(self.owner, error)

    def __await__(self) -> Generator[Any, None, "Effect"]:
        # Waits for every iteration of the effect to land, raising what one of them raised.
        if self.task is not None:
            yield from asyncio.wait([self.task]).__await__()
        if self.error is not None:
            raise self.error
        return self

    async def _run(self, activation: Activation) -> None:
        try:
            await execute(activation, self.accumulator, lambda: self.armed)
        except Exception as error:  # noqa: BLE001 - any failure is reported; cancellation is a BaseException and passes
            self.error = error
            self.owner.runtime.report(self.owner, error)
            if self.owner.state is FiberState.LOADING and self.owner.error is None:
                self.owner.error = error
            await self.dispose()

class Fiber:
    # One instantiation of a component: ⟨d, p, e, π, σ, τ, θ⟩ of Definition 49. Its uid is drawn fresh and never
    # reused, so a fiber that replaces another is never mistaken for it, even when both provide equal values.
    __slots__ = (
        "_retire",
        "accumulator",
        "committed",
        "component",
        "ctx",
        "error",
        "inertia",
        "origin",
        "parent",
        "pending",
        "realms",
        "removed",
        "retired",
        "runtime",
        "state",
        "table",
        "uid",
    )

    def __init__(
        self,
        runtime: "Runtime",
        uid: int,
        component: Component,
        parent: "Fiber | None",
        realms: Mapping[Key[Any], RealmId],
    ) -> None:
        from .context import Context
        self.runtime = runtime
        self.uid = uid
        self.component = component
        self.parent = parent
        # Realms are fixed when the fiber is inserted (Section 4.4, Isolation): its interface does not move under it.
        self.realms: Mapping[Key[Any], RealmId] = dict(realms)
        self.ctx: Context = Context(runtime, self, {}, ())
        # The context the fiber was instantiated through, so a revision is reinserted where the fiber stood.
        self.origin: Context = self.ctx
        # σₙ: the bindings this fiber has installed, by realm. Only its own provision stages write it.
        self.table: dict[RealmId, Binding] = {}
        self.retired = False
        self.state = FiberState.INACTIVE
        self.error: Exception | None = None
        # ω: the provider each declared key resolved to when the current activation began.
        self.committed: View | None = None
        self.accumulator = Accumulator()
        # The transition in flight, if any: once begun it runs to completion before the fiber responds again.
        self.inertia: asyncio.Task[None] | None = None
        # Effects whose iterations are still landing; an activation waits for them before the fiber is active.
        self.pending: set[asyncio.Task[None]] = set()
        self.removed = False
        self._retire: Inverse | None = None

    @property
    def name(self) -> str:
        return self.component.name

    @property
    def requirement_realms(self) -> frozenset[RealmId]:
        return frozenset(self.realms[key] for key in self.component.requires)

    def retire(self) -> None:
        # O-Retire: a request, carried out by the lifecycle rules. Retiring a fiber from outside its parent's
        # accumulator discards the parent's inverse for it, so it is never applied a second time.
        if self.parent is not None and self._retire is not None:
            self.parent.accumulator.discard(self._retire)
        self.runtime.retire(self)

    async def dispose(self) -> None:
        # Retires the fiber and waits until it, and everything it instantiated, has left the registry.
        self.retire()
        await self.runtime.wait_for(lambda: self.removed)

    async def revise(self, component: Component | None = None) -> "Fiber":
        # A revision (Section 4.4, Configuration): retire, let the lifecycle rules deactivate, remove, and reinsert
        # where this fiber stood, with a new component or the same one. It is how a failed fiber is retried, and the
        # new fiber is a new identity, so its dependents reactivate against it.
        await self.dispose()
        return self.origin.use(component if component is not None else self.component)

    def __repr__(self) -> str:
        return f"Fiber({self.name!r}, uid={self.uid}, {self.state.value})"

class Runtime:
    # The registry of fibers and the rules that move them (Section 4.2). Orchestration (use, retire) is the only
    # input; the lifecycle rules then fire unprompted whenever their premises hold, and no rule names a scheduler.
    def __init__(self, on_error: ErrorHandler = log_error) -> None:
        from .context import Context
        self._uids = itertools.count(1)
        self._fibers: dict[int, Fiber] = {}
        # The one fiber that may provide each realm, fixed by the provisions at insertion and not by the state.
        self._owners: dict[RealmId, Fiber] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._changed = asyncio.Event()
        self._on_error = on_error
        self.root_fiber = Fiber(self, 0, Component("root"), None, {})
        self.root_fiber.state = FiberState.ACTIVE
        self.root_fiber.committed = {}
        self.root: Context = Context(self, self.root_fiber, {}, ())
        self.root_fiber.ctx = self.root

    # --- reading the state ---------------------------------------------------------------------------------------

    def fibers(self) -> tuple[Fiber, ...]:
        return tuple(self._fibers.values())

    def children(self, fiber: Fiber) -> tuple[Fiber, ...]:
        return tuple(child for child in self._fibers.values() if child.parent is fiber)

    def owner(self, realm: RealmId) -> Fiber | None:
        return self._owners.get(realm)

    def provider(self, realm: RealmId) -> Fiber | None:
        # σ_γ is the union of the tables of the ACTIVE fibers alone (Equation 46): a fiber that has begun to leave
        # stops providing before any of its inverses runs.
        owner = self._owners.get(realm)
        if owner is not None and owner.state is FiberState.ACTIVE and realm in owner.table:
            return owner
        return None

    def target(self, fiber: Fiber) -> View | None:
        # Definition 53: None (⊥) when the fiber is retired or its declared keys are not all provided; otherwise
        # each declared key mapped to the fiber providing it.
        if fiber.retired:
            return None
        view: dict[Key[Any], Fiber] = {}
        for key in fiber.component.requires:
            provider = self.provider(fiber.realms[key])
            if provider is None:
                return None
            view[key] = provider
        return view

    def relied(self, fiber: Fiber) -> bool:
        # Definition 54: some other installed fiber resolves a key to this one.
        return any(
            other is not fiber and other.state in INSTALLED and other.committed is not None
            and any(provider is fiber for provider in other.committed.values())
            for other in self._fibers.values()
        )

    def describe(self) -> str:
        # How the registered fibers fit together, from their declarations alone: the problems that keep any of them
        # from activating, the order a load from scratch activates them in, their states, and who provides what.
        from .describe import Declared, describe
        declared: dict[Fiber, Declared] = {}
        for fiber in self._fibers.values():
            state = fiber.state.value + (", retired" if fiber.retired else "")
            if fiber.error is not None:
                state += f": {fiber.error}"
            declared[fiber] = Declared(
                fiber.name,
                tuple(fiber.component.requires),
                tuple(fiber.component.provides),
                fiber.realms,
                declared.get(fiber.parent) if fiber.parent is not None else None,
                state,
                inactive=fiber.retired,
            )
        return describe(list(declared.values()))

    def quiet(self) -> bool:
        # Definition 53: every fiber has settled at its target view, with no transition in progress.
        for fiber in self._fibers.values():
            if fiber.inertia is not None:
                return False
            target = self.target(fiber)
            if fiber.state is FiberState.INACTIVE and target is not None:
                return False
            if fiber.state is FiberState.ACTIVE and target != fiber.committed:
                return False
            if fiber.state in (FiberState.LOADING, FiberState.UNLOADING):
                return False
        return True

    # --- orchestration -------------------------------------------------------------------------------------------

    def insert(self, component: Component, ctx: "Context") -> Fiber:
        # O-Insert, under the fiber owning ctx. Its last premise is the single-source discipline: no realm gets a
        # second possible provider.
        parent = ctx.fiber
        if parent.removed:
            raise InsertionError(f"{parent.name} has left the registry, so nothing can be inserted under it")
        fixed = {key: ctx.realm(key) for key in (*component.requires, *component.provides)}
        for key in component.provides:
            other = self._owners.get(fixed[key])
            if other is not None:
                raise InsertionError(
                    f"{component.name} provides {key.name}, which {other.name} already provides in the same realm"
                )
        fiber = Fiber(self, next(self._uids), component, parent, fixed)
        fiber.ctx = ctx.derive(fiber)
        fiber.origin = ctx
        self._fibers[fiber.uid] = fiber
        for key in component.provides:
            self._owners[fixed[key]] = fiber
        self._signal()
        return fiber

    def instantiate(self, ctx: "Context", component: Component) -> Fiber:
        # Definition 52: an iteration of the parent that inserts a child, and yields the child's O-Retire as its
        # inverse. Retire rather than remove, because an inverse must apply wherever it is reached.
        parent = ctx.fiber
        self.check_effectful(parent)
        fiber = self.insert(component, ctx)

        def retire() -> None:
            self.retire(fiber)

        fiber._retire = retire
        parent.accumulator.push(retire)
        self.refresh(fiber)
        return fiber

    def retire(self, fiber: Fiber) -> None:
        if fiber.retired or fiber.removed:
            return
        fiber.retired = True
        self._signal()
        self.refresh(fiber)

    def _try_remove(self, fiber: Fiber) -> None:
        # O-Remove, taken as soon as its premises hold: retired, settled, holding no bindings, children gone first.
        if fiber.removed or fiber is self.root_fiber or not fiber.retired or fiber.inertia is not None:
            return
        if fiber.state not in SETTLED or fiber.table or self.children(fiber):
            return
        del self._fibers[fiber.uid]
        for key in fiber.component.provides:
            if self._owners.get(fiber.realms[key]) is fiber:
                del self._owners[fiber.realms[key]]
        fiber.removed = True
        self._signal()
        if fiber.parent is not None:
            self._try_remove(fiber.parent)

    # --- effects -------------------------------------------------------------------------------------------------

    def check_effectful(self, fiber: Fiber) -> None:
        if fiber.state not in (FiberState.LOADING, FiberState.ACTIVE) or fiber.removed:
            raise InactiveEffect(f"{fiber.name} is {fiber.state.value}, so an effect on it would never be reverted")

    def effect(self, owner: Fiber, callback: Callable[[], Activation], label: str) -> Effect:
        # ctx.effect: runs callback as an effect iterator and composes its disposal onto the owner's accumulator, so a
        # child effect's inverse is itself an effect on the owner (the recursive structure of ∂²Γ).
        self.check_effectful(owner)
        effect = Effect(owner, label)
        owner.accumulator.push(effect._inverse)
        try:
            activation = callback()
            if needs_awaiting(activation):
                effect.task = self._spawn(effect._run(activation))
                owner.pending.add(effect.task)
                effect.task.add_done_callback(owner.pending.discard)
            else:
                execute_now(activation, effect.accumulator, lambda: effect.armed)
        except Exception:
            # What ran before the failure is reverted at once; the failure itself installed nothing.
            effect.armed = False
            owner.accumulator.discard(effect._inverse)
            self._spawn(self._recover(owner, effect.accumulator))
            raise
        return effect

    async def _recover(self, owner: Fiber, accumulator: Accumulator) -> None:
        for error in await accumulator.recover():
            self.report(owner, error)

    def report(self, fiber: Fiber, error: Exception) -> None:
        self._on_error(fiber, error)

    # --- reactive notification and the lifecycle rules -----------------------------------------------------------

    def notify(self, realm: RealmId) -> None:
        # Algorithm 3: every binding change at a realm re-evaluates each fiber declaring a key resolved there. A
        # neutral change is harmless, refresh being idempotent.
        for fiber in tuple(self._fibers.values()):
            if not fiber.removed and realm in fiber.requirement_realms:
                self.refresh(fiber)

    def _notify_provisions(self, fiber: Fiber) -> None:
        for realm in tuple(fiber.table):
            self.notify(realm)

    def refresh(self, fiber: Fiber) -> None:
        # Compares the fiber's target view with its committed one and begins the transition that closes the gap. A
        # transition already in flight is left alone: it reads the target again at every seam and when it lands.
        if fiber.removed or fiber.inertia is not None or fiber is self.root_fiber:
            return
        target = self.target(fiber)
        if fiber.state is FiberState.INACTIVE and target is not None:
            # L-Begin: commit the view, then activate against it.
            fiber.state = FiberState.LOADING
            fiber.committed = target
            self._signal()
            fiber.inertia = self._spawn(self._drive(fiber))
        elif fiber.state is FiberState.ACTIVE and target != fiber.committed:
            self._leave(fiber)
            fiber.inertia = self._spawn(self._drive(fiber))
        else:
            self._try_remove(fiber)

    def _leave(self, fiber: Fiber) -> None:
        # L-Leave: stop providing, and run nothing yet. Dependents see their target turn and begin their own teardown
        # while every binding of this fiber is still in place for them to use.
        fiber.state = FiberState.UNLOADING
        self._signal()
        self._notify_provisions(fiber)

    async def _drive(self, fiber: Fiber) -> None:
        # One transition, chaining into the next until the fiber rests where its target says it should.
        try:
            while True:
                if fiber.state is FiberState.LOADING:
                    if await self._activate(fiber):
                        # L-Finish
                        fiber.state = FiberState.ACTIVE
                        self._signal()
                        self._notify_provisions(fiber)
                        if self.target(fiber) == fiber.committed:
                            return
                        self._leave(fiber)
                    else:
                        # L-Divert, or a failure leaving by the same route with what it installed so far.
                        fiber.state = FiberState.UNLOADING
                        self._signal()
                elif fiber.state is FiberState.UNLOADING:
                    await self._release(fiber)
                    target = self.target(fiber)
                    if fiber.error is not None or target is None:
                        return
                    # Unloading chains straight back into activating against the target it now has.
                    fiber.state = FiberState.LOADING
                    fiber.committed = target
                    self._signal()
                else:
                    return
        finally:
            fiber.inertia = None
            self._signal()
            self._try_remove(fiber)

    async def _activate(self, fiber: Fiber) -> bool:
        # L-Begin's activation as L-Iter steps: the fiber's effect function runs as an iterator against the one
        # resolution it committed to (Theorem 71), stopping at the first seam where that resolution no longer holds.
        committed = fiber.committed

        def unchanged() -> bool:
            return self.target(fiber) == committed

        try:
            if fiber.component.apply is not None:
                await execute(fiber.component.apply(fiber.ctx), fiber.accumulator, unchanged)
            # Effects begun during activation land before the fiber counts as active, so it provides what they install.
            while pending := {task for task in fiber.pending if not task.done()}:
                await asyncio.wait(pending)
        except Exception as error:  # noqa: BLE001 - any failure is reported; cancellation is a BaseException and passes
            fiber.error = error
            self.report(fiber, error)
        return fiber.error is None and unchanged()

    async def _release(self, fiber: Fiber) -> None:
        # L-Unload, behind its guard: the inverses run only once no installed fiber resolves a key to this one, so
        # every consumer has finished a teardown that may still have needed it.
        await self.wait_for(lambda: not self.relied(fiber))
        for error in await fiber.accumulator.recover():
            self.report(fiber, error)
        fiber.committed = None
        fiber.state = FiberState.FAILED if fiber.error is not None else FiberState.INACTIVE
        self._signal()

    # --- waiting -------------------------------------------------------------------------------------------------

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _signal(self) -> None:
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def wait_for(self, condition: Callable[[], bool]) -> None:
        while not condition():
            await self._changed.wait()

    async def settled(self, under: Fiber | None = None) -> None:
        # Waits until no transition or effect is in flight; with acyclic dependencies that is quiescence (Theorem 73).
        # Given a fiber, waits only for it and what it instantiated, so an orchestrator of one subtree, such as a
        # session, is not held up by the rest of the system.
        if under is None:
            while pending := {task for task in self._tasks if not task.done()}:
                await asyncio.wait(pending)
            return

        def subtree(fiber: Fiber) -> list[Fiber]:
            return [fiber, *(member for child in self.children(fiber) for member in subtree(child))]

        while True:
            members = subtree(under)
            pending = {task for fiber in members for task in (fiber.inertia, *fiber.pending)
                       if task is not None and not task.done()}
            if not pending:
                return
            await asyncio.wait(pending)

    async def close(self) -> None:
        # Reverts everything the root did, which retires every top-level fiber, and waits for them all to leave.
        await self._recover(self.root_fiber, self.root_fiber.accumulator)
        await self.settled()

@asynccontextmanager
async def open_runtime(on_error: ErrorHandler = log_error) -> AsyncIterator["Runtime"]:
    runtime = Runtime(on_error)
    try:
        yield runtime
    finally:
        await runtime.close()
