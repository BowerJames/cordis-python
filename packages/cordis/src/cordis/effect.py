import contextlib
import contextvars
import inspect
import warnings
from typing import AsyncIterator, Awaitable, Callable, Iterable, Iterator, TypeGuard

from .errors import DroppedInverse

# The inverse an effect yields (Definition 8): it must revert the effect at the state the effect produced, and nothing
# more is asked of it. A closure over what the effect created is exactly that. It may be a coroutine function.
type Inverse = Callable[[], Awaitable[None] | None]

# What running an effect function returns. A plain inverse (or None, for an effect with nothing to undo) is one
# iteration; an iterator, sync or async, is an effect iterator (Definition 17), each item one iteration's inverse, or
# None for a seam with nothing to undo. An awaitable is one iteration that has to be waited for.
type Activation = (
    Inverse
    | Iterator[Inverse | None]
    | AsyncIterator[Inverse | None]
    | Awaitable[Inverse | None]
    | None
)

# Consulted before every iteration: once it trips, iteration stops at that seam and only the inverses accumulated so
# far remain (the L-Divert of Section 4.2.2).
type Guard = Callable[[], bool]

# Consulted after every iteration: raises when the iteration left an inverse it made unyielded.
type Check = Callable[[], None]

def _unchecked() -> None:
    pass

class Step:
    # One step of a fiber's effect function, while the runtime drives it: every Undo made meanwhile is owed by the
    # fiber, whoever made it, so a registry operation is held to a yield like provide and use are. A step ends when its
    # iteration lands, and a task the step started does not carry it on: what that task makes later is owed by nobody,
    # and is adopted by whoever makes it.
    __slots__ = ("active", "owe")

    def __init__(self, owe: "Callable[[Undo], None]") -> None:
        self.active = True
        self.owe = owe

_STEP: contextvars.ContextVar[Step | None] = contextvars.ContextVar("cordis_step", default=None)

@contextlib.contextmanager
def stepping(owe: "Callable[[Undo], None] | None") -> Iterator[None]:
    # Runs what it wraps as one step owed to owe, or as nobody's step when owe is None.
    step = Step(owe) if owe is not None else None
    token = _STEP.set(step)
    try:
        yield
    finally:
        if step is not None:
            step.active = False
        _STEP.reset(token)

def unowned() -> contextvars.Context:
    # A context for a task of the runtime's own, so nothing it makes is owed by the step that started it.
    context = contextvars.copy_context()
    context.run(_STEP.set, None)
    return context

async def run_inverse(inverse: Inverse) -> None:
    result = inverse()
    if inspect.isawaitable(result):
        await result

class Undo:
    # An inverse that knows what became of it. Whatever performs an effect on a component's behalf returns one, and
    # one made during a step of an effect function is owed by that fiber: it yields it, or adopts it. Until then it is
    # outstanding. Applying it reverts the effect at most once, and
    # takes it off the accumulator holding it, so an effect undone early is never undone again. One that is dropped
    # while outstanding says so, as a coroutine that was never awaited does.
    __slots__ = ("_covers", "_run", "_settled", "applied", "holder", "label")

    def __init__(self, run: Inverse, label: str = "an effect", covers: Iterable[Inverse] = ()) -> None:
        self._run = run
        self.label = label
        self.applied = False
        # What answers for it: the accumulator it was yielded onto, or an undo that applies it as part of itself.
        self.holder: Accumulator | Undo | None = None
        # The undos this one applies itself: they are its to answer for, and stop being owed separately.
        self._covers = tuple(covered for covered in covers if isinstance(covered, Undo))
        for covered in self._covers:
            if covered.holder is not None:
                raise ValueError(f"the inverse of {covered.label} is already held, so nothing else can cover it")
            covered.holder = self
        # Told once, when the undo first lands on an accumulator or is applied: by then nobody owes it.
        self._settled: Callable[[Undo], None] | None = None
        step = _STEP.get()
        if step is not None and step.active:
            step.owe(self)

    @property
    def outstanding(self) -> bool:
        # Neither applied nor on an accumulator, whether itself or through an undo covering it.
        undo = self
        while not undo.applied:
            holder = undo.holder
            if holder is None:
                return True
            if not isinstance(holder, Undo):
                return False
            undo = holder
        return False

    def settle(self) -> None:
        settled, self._settled = self._settled, None
        if settled is not None:
            settled(self)
        for covered in self._covers:
            covered.settle()

    def __call__(self) -> Awaitable[None] | None:
        if self.applied:
            return None
        self.applied = True
        holder, self.holder = self.holder, None
        if isinstance(holder, Accumulator):
            holder.discard(self)
        self.settle()
        return self._run()

    def __del__(self) -> None:
        if self.applied or self.holder is not None:
            return
        # The interpreter may be shutting down, with nothing left to warn through.
        with contextlib.suppress(Exception):
            warnings.warn(f"the inverse of {self.label} was never yielded, adopted or applied",
                          DroppedInverse, stacklevel=2)

    def __repr__(self) -> str:
        state = "applied" if self.applied else "outstanding" if self.outstanding else "held"
        return f"Undo({self.label!r}, {state})"

def compose(*inverses: Inverse, label: str | None = None) -> Undo:
    # One inverse for several effects made in the order given: applying it applies them last first, each to the end
    # even when another fails. For a helper that performs more than one effect and has one inverse to give back.
    name = label if label is not None else ", ".join(
        inverse.label if isinstance(inverse, Undo) else "an effect" for inverse in inverses)

    def run() -> Awaitable[None] | None:
        remaining = list(inverses)
        errors: list[Exception] = []

        def finish() -> None:
            if len(errors) == 1:
                raise errors[0]
            if errors:
                raise ExceptionGroup(f"undoing {name}", errors)

        async def rest(waited: Awaitable[None]) -> None:
            try:
                await waited
            except Exception as error:  # noqa: BLE001 - reported once every inverse has run
                errors.append(error)
            while remaining:
                try:
                    await run_inverse(remaining.pop())
                except Exception as error:  # noqa: BLE001 - reported once every inverse has run
                    errors.append(error)
            finish()

        while remaining:
            try:
                result = remaining.pop()()
            except Exception as error:  # noqa: BLE001 - reported once every inverse has run
                errors.append(error)
                continue
            if inspect.isawaitable(result):
                return rest(result)
        finish()
        return None

    return Undo(run, name, covers=inverses)

class Accumulator:
    # φ: the composite of the inverses performed so far. Pushing composes an inverse on (φ ∘ g); recovering applies
    # them one at a time, last in first out, so each meets the state its own effect produced (Theorem 16).
    __slots__ = ("_inverses",)

    def __init__(self) -> None:
        self._inverses: list[Undo] = []

    def push(self, inverse: Inverse, label: str = "an effect") -> Undo:
        # Takes an inverse onto the accumulator, and gives back the undo now holding its place: applying that early
        # takes it off again. One already here stays where it is, and one already applied has nothing left to undo.
        undo = inverse if isinstance(inverse, Undo) else Undo(inverse, label)
        if undo.applied or undo.holder is self:
            return undo
        if undo.holder is not None:
            raise ValueError(f"the inverse of {undo.label} is already held, so it cannot be yielded again")
        undo.holder = self
        self._inverses.append(undo)
        undo.settle()
        return undo

    def discard(self, inverse: Inverse) -> None:
        # Drops an inverse that was applied out of order, so it is never applied a second time: applying it again
        # would meet a state no application of its effect produced.
        for index in range(len(self._inverses) - 1, -1, -1):
            if self._inverses[index] is inverse:
                del self._inverses[index]
                return

    def __len__(self) -> int:
        return len(self._inverses)

    async def recover(self) -> list[Exception]:
        # Applies every inverse, newest first, until none is left; an inverse that fails is reported, and the rest
        # still run, since a teardown that stops half way strands everything below it.
        errors: list[Exception] = []
        while self._inverses:
            undo = self._inverses.pop()
            undo.holder = None
            try:
                await run_inverse(undo)
            except Exception as error:  # noqa: BLE001 - any failure is reported; cancellation is a BaseException and passes
                errors.append(error)
        return errors

def is_async_iterator(activation: object) -> TypeGuard[AsyncIterator[Inverse | None]]:
    return hasattr(activation, "__anext__")

def is_iterator(activation: object) -> TypeGuard[Iterator[Inverse | None]]:
    return hasattr(activation, "__next__")

def _accumulate(accumulator: Accumulator, inverse: object) -> None:
    if inverse is None:
        return
    if not callable(inverse):
        raise TypeError(f"an effect yields its inverse as a callable, or None for a seam; got {inverse!r}")
    accumulator.push(inverse)

def execute_now(
    activation: Activation,
    accumulator: Accumulator,
    guard: Guard,
    check: Check = _unchecked,
    owe: Callable[[Undo], None] | None = None,
) -> bool:
    # Drives an activation that needs no awaiting (Algorithm 1, synchronously). Returns whether it ran to completion
    # rather than stopping at a seam because the guard tripped. Every iteration ends at a seam, where check is
    # consulted: by then the iteration has yielded the inverse of whatever it did.
    if activation is None:
        check()
        return True
    if is_iterator(activation):
        try:
            while guard():
                try:
                    with stepping(owe):
                        inverse = next(activation)
                except StopIteration:
                    check()
                    return True
                _accumulate(accumulator, inverse)
                check()
            return False
        finally:
            close = getattr(activation, "close", None)
            if close is not None:
                close()
    if is_async_iterator(activation) or inspect.isawaitable(activation):
        raise TypeError("an activation that has to be awaited cannot be executed synchronously")
    _accumulate(accumulator, activation)
    check()
    return True

async def execute(
    activation: Activation,
    accumulator: Accumulator,
    guard: Guard,
    check: Check = _unchecked,
    owe: Callable[[Undo], None] | None = None,
) -> bool:
    # Algorithm 1: drives an activation of any form, folding each inverse it yields into the accumulator. An iteration
    # already in flight is let land (the host is inertial, Section 4.4); the guard is read between iterations.
    if is_async_iterator(activation):
        try:
            while guard():
                try:
                    with stepping(owe):
                        inverse = await anext(activation)
                except StopAsyncIteration:
                    check()
                    return True
                _accumulate(accumulator, inverse)
                check()
            return False
        finally:
            aclose = getattr(activation, "aclose", None)
            if aclose is not None:
                await aclose()
    if not is_iterator(activation) and inspect.isawaitable(activation):
        with stepping(owe):
            inverse = await activation
        _accumulate(accumulator, inverse)
        check()
        return True
    return execute_now(activation, accumulator, guard, check, owe)
