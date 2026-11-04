import asyncio

import pytest

from plugins import Events, Topic

PINGS: Topic[str] = Topic("pings")

async def test_every_handler_is_called_concurrently_and_publish_waits_for_them_all() -> None:
    events = Events()
    log: list[str] = []
    gate = asyncio.Event()

    async def slow(event: str) -> None:
        await gate.wait()
        log.append(f"slow {event}")

    async def fast(event: str) -> None:
        log.append(f"fast {event}")
        gate.set()

    events.subscribe(PINGS, slow)
    events.subscribe(PINGS, fast)
    await events.publish(PINGS, "a")
    assert log == ["fast a", "slow a"]

async def test_a_topic_with_no_handlers_returns_at_once() -> None:
    await Events().publish(PINGS, "nobody")

async def test_a_failing_handler_stops_none_of_the_others_and_every_failure_is_raised_together() -> None:
    events = Events()
    log: list[str] = []

    async def broken(event: str) -> None:
        raise ValueError("broken")

    async def fine(event: str) -> None:
        log.append(event)

    events.subscribe(PINGS, broken)
    events.subscribe(PINGS, fine)
    with pytest.raises(ExceptionGroup) as raised:
        await events.publish(PINGS, "a")
    assert log == ["a"]
    [error] = raised.value.exceptions
    assert "of topic pings" in error.__notes__[0]

async def test_two_subscriptions_of_one_handler_withdraw_independently() -> None:
    # Each subscription is an entry of its own (Definition 46), so equal handlers stay independent.
    events = Events()
    log: list[str] = []

    async def handler(event: str) -> None:
        log.append(event)

    first = events.subscribe(PINGS, handler)
    events.subscribe(PINGS, handler)
    await events.publish(PINGS, "a")
    first()
    await events.publish(PINGS, "b")
    assert log == ["a", "a", "b"]

async def test_topics_with_the_same_name_are_different_topics() -> None:
    events = Events()
    log: list[str] = []

    async def handler(event: str) -> None:
        log.append(event)

    events.subscribe(Topic[str]("pings"), handler)
    await events.publish(PINGS, "a")
    assert log == []
