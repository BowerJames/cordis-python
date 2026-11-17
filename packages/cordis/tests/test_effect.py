import gc
import warnings
from typing import AsyncIterator, Iterator

import pytest

from cordis import DroppedInverse, Undo, compose
from cordis.effect import Accumulator, Inverse, execute, execute_now


def recording(log: list[str], name: str) -> Inverse:
    def inverse() -> None:
        log.append(name)
    return inverse

async def test_recover_applies_inverses_last_in_first_out() -> None:
    # Twisted composition: (f₁, g₁) ∘ (f₂, g₂) = (f₁ ∘ f₂, g₂ ∘ g₁), so socks go on before shoes and come off after.
    log: list[str] = []
    accumulator = Accumulator()
    for name in ("socks", "shoes", "laces"):
        accumulator.push(recording(log, name))
    assert await accumulator.recover() == []
    assert log == ["laces", "shoes", "socks"]
    assert len(accumulator) == 0

async def test_recover_awaits_coroutine_inverses_one_at_a_time() -> None:
    log: list[str] = []
    accumulator = Accumulator()

    def asynchronous(name: str) -> Inverse:
        async def inverse() -> None:
            log.append(f"{name} begins")
            log.append(f"{name} ends")
        return inverse

    accumulator.push(asynchronous("first"))
    accumulator.push(asynchronous("second"))
    await accumulator.recover()
    assert log == ["second begins", "second ends", "first begins", "first ends"]

async def test_a_failing_inverse_is_reported_and_the_rest_still_run() -> None:
    log: list[str] = []
    accumulator = Accumulator()
    accumulator.push(recording(log, "first"))

    def broken() -> None:
        raise RuntimeError("cannot close")

    accumulator.push(broken)
    accumulator.push(recording(log, "third"))
    errors = await accumulator.recover()
    assert [str(error) for error in errors] == ["cannot close"]
    assert log == ["third", "first"]

async def test_an_inverse_applied_early_leaves_the_accumulator_and_is_never_applied_again() -> None:
    log: list[str] = []
    accumulator = Accumulator()
    first = accumulator.push(recording(log, "first"))
    accumulator.push(recording(log, "second"))
    first()
    first()
    assert len(accumulator) == 1
    await accumulator.recover()
    assert log == ["first", "second"]

async def test_an_undo_is_outstanding_until_an_accumulator_holds_it() -> None:
    log: list[str] = []
    undo = Undo(recording(log, "undone"), "an effect")
    assert undo.outstanding
    accumulator = Accumulator()
    assert accumulator.push(undo) is undo
    assert accumulator.push(undo) is undo  # already there, so it stays where it is
    assert not undo.outstanding
    assert len(accumulator) == 1
    with pytest.raises(ValueError, match="already held"):
        Accumulator().push(undo)
    await accumulator.recover()
    assert log == ["undone"]

async def test_a_composed_inverse_applies_its_parts_last_first_and_answers_for_them() -> None:
    log: list[str] = []
    socks, shoes = Undo(recording(log, "socks"), "socks"), Undo(recording(log, "shoes"), "shoes")

    async def laces() -> None:
        log.append("laces")

    dressed = compose(socks, shoes, laces)
    assert dressed.label == "socks, shoes, an effect"
    # Covered, but by something nobody holds yet: still owed.
    assert socks.outstanding and dressed.outstanding
    accumulator = Accumulator()
    accumulator.push(dressed)
    assert not socks.outstanding
    await accumulator.recover()
    assert log == ["laces", "shoes", "socks"]

async def test_a_composed_inverse_runs_every_part_even_when_one_fails() -> None:
    log: list[str] = []

    def broken() -> None:
        raise RuntimeError("cannot close")

    together = compose(recording(log, "first"), broken, recording(log, "third"))
    with pytest.raises(RuntimeError, match="cannot close"):
        together()
    assert log == ["third", "first"]

def test_an_inverse_dropped_without_being_held_or_applied_says_so() -> None:
    def dropped() -> list[warnings.WarningMessage]:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Undo(lambda: None, "subscribe to messages")
            gc.collect()
        return caught

    assert [str(warning.message) for warning in dropped()] == [
        "the inverse of subscribe to messages was never yielded, adopted or applied"]
    assert all(issubclass(warning.category, DroppedInverse) for warning in dropped())

def test_an_inverse_that_was_applied_or_held_is_dropped_quietly() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Undo(lambda: None, "applied")()
        Accumulator().push(Undo(lambda: None, "held"))
        compose(Undo(lambda: None, "covered"))()
        gc.collect()
    assert caught == []

def test_an_iterator_contributes_one_inverse_per_iteration() -> None:
    log: list[str] = []
    accumulator = Accumulator()

    def activation() -> Iterator[Inverse | None]:
        log.append("open")
        yield recording(log, "close")
        yield None  # a seam with nothing to undo
        log.append("listen")
        yield recording(log, "stop listening")

    assert execute_now(activation(), accumulator, lambda: True)
    assert len(accumulator) == 2

def test_the_check_is_consulted_at_every_seam_and_at_the_end() -> None:
    seams: list[int] = []
    accumulator = Accumulator()

    def activation() -> Iterator[Inverse | None]:
        yield lambda: None
        yield None

    assert execute_now(activation(), accumulator, lambda: True, lambda: seams.append(len(accumulator)))
    assert seams == [1, 1, 1]

def test_a_tripped_guard_stops_at_the_next_seam_keeping_what_was_done() -> None:
    log: list[str] = []
    accumulator = Accumulator()
    steps = iter([True, False])

    def activation() -> Iterator[Inverse | None]:
        try:
            log.append("first step")
            yield recording(log, "undo first")
            log.append("second step")
            yield recording(log, "undo second")
        finally:
            log.append("closed")

    assert not execute_now(activation(), accumulator, lambda: next(steps))
    assert log == ["first step", "closed"]
    assert len(accumulator) == 1

async def test_async_iterators_and_awaitables_are_executed_alike() -> None:
    log: list[str] = []
    accumulator = Accumulator()

    async def iterator() -> AsyncIterator[Inverse | None]:
        yield recording(log, "one")
        yield recording(log, "two")

    async def awaitable() -> Inverse:
        return recording(log, "three")

    assert await execute(iterator(), accumulator, lambda: True)
    assert await execute(awaitable(), accumulator, lambda: True)
    assert await execute(recording(log, "four"), accumulator, lambda: True)
    assert await execute(None, accumulator, lambda: True)
    await accumulator.recover()
    assert log == ["four", "three", "two", "one"]

def test_yielding_something_that_is_not_an_inverse_is_refused() -> None:
    def activation() -> Iterator[object]:
        yield "not callable"

    with pytest.raises(TypeError, match="callable"):
        execute_now(activation(), Accumulator(), lambda: True)  # type: ignore[arg-type]
