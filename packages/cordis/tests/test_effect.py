from typing import AsyncIterator, Iterator

import pytest

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

async def test_discard_drops_exactly_the_inverse_given() -> None:
    log: list[str] = []
    accumulator = Accumulator()
    first, second = recording(log, "first"), recording(log, "second")
    accumulator.push(first)
    accumulator.push(second)
    accumulator.discard(first)
    accumulator.discard(first)
    await accumulator.recover()
    assert log == ["second"]

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
