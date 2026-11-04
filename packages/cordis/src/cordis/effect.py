import inspect
from typing import AsyncIterator, Awaitable, Callable, Iterator, TypeGuard, cast

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

async def run_inverse(inverse: Inverse) -> None:
    result = inverse()
    if inspect.isawaitable(result):
        await result

class Accumulator:
    # φ: the composite of the inverses performed so far. Pushing composes an inverse on (φ ∘ g); recovering applies
    # them one at a time, last in first out, so each meets the state its own effect produced (Theorem 16).
    __slots__ = ("_inverses",)

    def __init__(self) -> None:
        self._inverses: list[Inverse] = []

    def push(self, inverse: Inverse) -> None:
        self._inverses.append(inverse)

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
            inverse = self._inverses.pop()
            try:
                await run_inverse(inverse)
            except Exception as error:  # noqa: BLE001 - any failure is reported; cancellation is a BaseException and passes
                errors.append(error)
        return errors

def is_async_iterator(activation: object) -> TypeGuard[AsyncIterator[Inverse | None]]:
    return hasattr(activation, "__anext__")

def is_iterator(activation: object) -> TypeGuard[Iterator[Inverse | None]]:
    return hasattr(activation, "__next__")

def needs_awaiting(activation: Activation) -> bool:
    return is_async_iterator(activation) or (not is_iterator(activation) and inspect.isawaitable(activation))

def _accumulate(accumulator: Accumulator, inverse: object) -> None:
    if inverse is None:
        return
    if not callable(inverse):
        raise TypeError(f"an effect yields its inverse as a callable, or None for a seam; got {inverse!r}")
    accumulator.push(cast(Inverse, inverse))

def execute_now(activation: Activation, accumulator: Accumulator, guard: Guard) -> bool:
    # Drives an activation that needs no awaiting (Algorithm 1, synchronously). Returns whether it ran to completion
    # rather than stopping at a seam because the guard tripped.
    if activation is None:
        return True
    if is_iterator(activation):
        try:
            while guard():
                try:
                    inverse = next(activation)
                except StopIteration:
                    return True
                _accumulate(accumulator, inverse)
            return False
        finally:
            close = getattr(activation, "close", None)
            if close is not None:
                close()
    if needs_awaiting(activation):
        raise TypeError("an activation that has to be awaited cannot be executed synchronously")
    _accumulate(accumulator, activation)
    return True

async def execute(activation: Activation, accumulator: Accumulator, guard: Guard) -> bool:
    # Algorithm 1: drives an activation of any form, folding each inverse it yields into the accumulator. An iteration
    # already in flight is let land (the host is inertial, Section 4.4); the guard is read between iterations.
    if is_async_iterator(activation):
        try:
            while guard():
                try:
                    inverse = await anext(activation)
                except StopAsyncIteration:
                    return True
                _accumulate(accumulator, inverse)
            return False
        finally:
            aclose = getattr(activation, "aclose", None)
            if aclose is not None:
                await aclose()
    if not is_iterator(activation) and inspect.isawaitable(activation):
        _accumulate(accumulator, await activation)
        return True
    return execute_now(activation, accumulator, guard)
