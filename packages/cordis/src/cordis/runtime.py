import asyncio
import inspect
import itertools
import logging
from contextlib import asynccontextmanager
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Coroutine, Mapping

from .component import Component
from .effect import Accumulator, Inverse, Undo, execute, stepping, unowned
from .entries import Entries
from .errors import InactiveEffect, InsertionError, UnyieldedInverse
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
# Stands for a configuration not given to a revision, which keeps the one the fiber has.
_KEEP: Any = object()

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

class Fiber:
    # One instantiation of a component: ⟨d, p, e, π, σ, τ, θ⟩ of Definition 49. Its uid is drawn fresh and never
    # reused, so a fiber that replaces another is never mistaken for it, even when both provide equal values.
    __slots__ = (
        "acceptors",
        "accumulator",
        "committed",
        "component",
        "config",
        "ctx",
        "error",
        "inertia",
        "issued",
        "origin",
        "parent",
        "realms",
        "removed",
        "retire",
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
        component: Component[Any],
        parent: "Fiber | None",
        realms: Mapping[Key[Any], RealmId],
        config: Any = None,
    ) -> None:
        from .context import Context
        self.runtime = runtime
        self.uid = uid
        self.component = component
        # The payload bound into the effect function (Algorithm 4, line 9): every activation applies the component to
        # the configuration the fiber holds as it begins, so one taken in place is the one a later reactivation reads.
        self.config: Any = config
        # How the fiber, while active, takes a new configuration without reloading (Context.accept).
        self.acceptors: Entries[Callable[[Any], bool | Awaitable[bool]]] = Entries()
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
        # The inverses made during this fiber's steps that it has neither yielded nor adopted yet, in the order they
        # were made. The fiber answers for them all the same: none is ever lost, only reported.
        self.issued: dict[Undo, None] = {}
        # The transition in flight, if any: once begun it runs to completion before the fiber responds again.
        self.inertia: asyncio.Task[None] | None = None
        self.removed = False
        # O-Retire, a request carried out by the lifecycle rules, and the inverse of the instantiation that made
        # this fiber (Definition 52): whoever instantiated it yields this, or adopts it.
        # Made during the step that instantiates it, so that step's fiber owes it.
        self.retire: Undo = Undo(lambda: runtime.retire(self), f"use {component.name}")

    @property
    def name(self) -> str:
        return self.component.name

    @property
    def requirement_realms(self) -> frozenset[RealmId]:
        return frozenset(self.realms[key] for key in self.component.requires)

    def outstanding(self) -> list[Undo]:
        return [undo for undo in self.issued if undo.outstanding]

    def owe(self, undo: Undo) -> None:
        # An Undo made during one of this fiber's steps, or by the kernel for this fiber outside any step: the fiber
        # answers for it until it is held or applied. One already owed stays with whoever owes it.
        if undo._settled is not None or undo.applied or undo.holder is not None:
            return
        self.issued[undo] = None
        undo._settled = self.settle

    def settle(self, undo: Undo) -> None:
        # An inverse this fiber owed has landed on an accumulator, or been applied: it is owed no longer.
        self.issued.pop(undo, None)

    async def dispose(self) -> None:
        # Retires the fiber and waits until it, and everything it instantiated, has left the registry. Retiring it
        # here takes its retire off whatever accumulator holds it, so it is never applied a second time.
        self.retire()
        await self.runtime.wait_for(lambda: self.removed)

    async def revise(self, component: Component[Any] | None = None, config: Any = _KEEP) -> "Fiber":
        # A revision (Section 4.4, Configuration): retire, let the lifecycle rules deactivate, remove, and reinsert
        # where this fiber stood, with a new component or configuration, or the same ones. It is how a failed fiber
        # is retried, and the new fiber is a new identity, so its dependents reactivate against it. A new component
        # is given its own configuration, or its defaults.
        await self.dispose()
        if component is None:
            revised = self.origin.use(self.component, self.config if config is _KEEP else config)
        else:
            revised = self.origin.use(component, component.configure() if config is _KEEP else config)
        # A revision is the orchestrator's doing, with no effect function to yield from: the fiber it stands under
        # holds its retire from here.
        self.origin.adopt(revised.retire)
        return revised

    async def update(self, config: Any) -> "Fiber":
        # Hands the fiber a new configuration (Section 5.2.1), and gives back the fiber that runs with it: this one,
        # when the configuration could be taken without a reload, or else the revision that reinserted it. Each route
        # ends where a load of the new configuration from scratch would have (Theorem 80), and takes the shortest:
        #   - an equal configuration changes nothing;
        #   - a fiber resting inactive has installed nothing, so it takes the payload as it stands, and activates with
        #     it once its dependencies are there;
        #   - an active fiber whose every acceptor takes the payload goes on as it is (Context.accept);
        #   - anything else, a fiber that failed or is in a transition included, is revised.
        if config == self.config:
            return self
        if self.state is FiberState.INACTIVE and self.inertia is None and not self.retired:
            self.config = config
            return self
        if self.state is FiberState.ACTIVE and self.inertia is None and not self.retired and len(self.acceptors):
            previous, self.config = self.config, config
            try:
                for acceptor in self.acceptors:
                    taken = acceptor(config)
                    if inspect.isawaitable(taken):
                        taken = await taken
                    if not taken:
                        break
                else:
                    return self
            except Exception as error:  # noqa: BLE001 - an acceptor that fails asks for a revision, as declining does
                self.runtime.report(self, error)
            # Declined: the revision starts from the configuration the fiber ran with.
            self.config = previous
        return await self.revise(config=config)

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
        self.root_fiber.retire.applied = True  # the root is never retired
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

    def insert(self, component: Component[Any], ctx: "Context", config: Any = None) -> Fiber:
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
        fiber = Fiber(self, next(self._uids), component, parent, fixed, config)
        fiber.ctx = ctx.derive(fiber)
        fiber.origin = ctx
        self._fibers[fiber.uid] = fiber
        for key in component.provides:
            self._owners[fixed[key]] = fiber
        self._signal()
        return fiber

    def instantiate(self, ctx: "Context", component: Component[Any], config: Any = None) -> Fiber:
        # Definition 52: an iteration of the parent that inserts a child, and yields the child's O-Retire as its
        # inverse. Retire rather than remove, because an inverse must apply wherever it is reached.
        parent = ctx.fiber
        self.check_effectful(parent)
        fiber = self.insert(component, ctx, config)
        if parent is self.root_fiber:
            # The root is the orchestrator: it has no effect function to yield from, so it holds what it makes.
            parent.accumulator.push(fiber.retire)
        else:
            # Owed by the step that made it, or else by the parent: the kernel knows whose instantiation this is.
            parent.owe(fiber.retire)
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

    def adopt(self, fiber: Fiber, inverse: Inverse, label: str = "an effect") -> Undo:
        self.check_effectful(fiber)
        return fiber.accumulator.push(inverse, label)

    async def _recover(self, owner: Fiber, accumulator: Accumulator) -> None:
        for error in await accumulator.recover():
            self.report(owner, error)

    def report(self, fiber: Fiber, error: Exception) -> None:
        self._on_error(fiber, error)

    def fail(self, fiber: Fiber, error: Exception) -> None:
        # Section 4.4's failure, read at a fiber that is activating or active rather than at a raising iteration: an
        # activation stops at its next seam, and an active fiber leaves (L-Leave) as one whose target has turned does.
        # Either way it unloads by the route every deactivation takes, and rests failed.
        self.report(fiber, error)
        if fiber.removed or fiber.error is not None:
            return
        if fiber.state is FiberState.LOADING:
            fiber.error = error
            self._signal()
        elif fiber.state is FiberState.ACTIVE:
            fiber.error = error
            self._leave(fiber)
            if fiber.inertia is None:
                fiber.inertia = self._spawn(self._drive(fiber))

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

        def proceeding() -> bool:
            # The guard read at every seam: the resolution still holds, and nothing the fiber started has failed.
            return fiber.error is None and unchanged()

        def yielded() -> None:
            # At every seam the accumulator holds the inverse of everything done so far, which is what lets the
            # activation stop there and recover exactly the steps taken.
            owed = fiber.outstanding()
            if owed:
                raise UnyieldedInverse(f"{fiber.name} did not yield the inverse of "
                                       f"{', '.join(undo.label for undo in owed)} before its next step")

        try:
            if fiber.component.apply is not None:
                # Calling the effect function is a step of its own: a plain function does all its work here.
                with stepping(fiber.owe):
                    activation = fiber.component.apply(fiber.ctx, fiber.config)
                await execute(activation, fiber.accumulator, proceeding, yielded, fiber.owe)
        except Exception as error:  # noqa: BLE001 - any failure is reported; cancellation is a BaseException and passes
            fiber.error = error
            self.report(fiber, error)
        return fiber.error is None and unchanged()

    async def _release(self, fiber: Fiber) -> None:
        # L-Unload, behind its guard: the inverses run only once no installed fiber resolves a key to this one, so
        # every consumer has finished a teardown that may still have needed it.
        await self.wait_for(lambda: not self.relied(fiber))
        # What the fiber still owes is reverted with the rest, newest first: an inverse left unyielded by a failed
        # activation, which has said so already, or one never adopted while the fiber was active, which is said here.
        owed = fiber.outstanding()
        if owed and fiber.error is None:
            self.report(fiber, UnyieldedInverse(f"{fiber.name} never yielded or adopted the inverse of "
                                                f"{', '.join(undo.label for undo in owed)}"))
        for undo in owed:
            fiber.accumulator.push(undo)
        fiber.issued.clear()
        for error in await fiber.accumulator.recover():
            self.report(fiber, error)
        fiber.committed = None
        fiber.state = FiberState.FAILED if fiber.error is not None else FiberState.INACTIVE
        self._signal()

    # --- waiting -------------------------------------------------------------------------------------------------

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        # Always started lazily, whatever task factory the loop has: the runtime records a task (a transition in
        # flight) before any of it runs, as Algorithm 5 does. A task started eagerly could run to the
        # end inside this call, and leave a finished transition recorded as in flight, so the fiber could never leave.
        # Run outside whatever step started it, so nothing a transition makes is owed by the fiber that caused it.
        task = asyncio.Task(coroutine, loop=asyncio.get_running_loop(), eager_start=False, context=unowned())
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
        # Waits until no transition is in flight; with acyclic dependencies that is quiescence (Theorem 73).
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
            pending = {fiber.inertia for fiber in members if fiber.inertia is not None and not fiber.inertia.done()}
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
