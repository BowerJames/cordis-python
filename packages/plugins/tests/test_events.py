# The event bus contract, run against every component keeping it, through the contract alone.
import asyncio
from typing import AsyncIterator

import pytest

from cordis_plugins import EVENTS, Events, Topic, event_bus
from cordis_plugins.testing import provided

PINGS: Topic[str] = Topic("pings")

@pytest.fixture(params=[event_bus], ids=lambda keeper: keeper.__name__)
async def events(request: pytest.FixtureRequest) -> AsyncIterator[Events]:
    async with provided(EVENTS, request.param) as kept:
        yield kept

async def test_every_handler_is_called_concurrently_and_publish_waits_for_them_all(events: Events) -> None:
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

async def test_a_topic_with_no_handlers_returns_at_once(events: Events) -> None:
    await events.publish(PINGS, "nobody")

async def test_a_failing_handler_stops_none_of_the_others_and_every_failure_is_raised_together(events: Events) -> None:
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

async def test_two_subscriptions_of_one_handler_withdraw_independently(events: Events) -> None:
    # Each subscription is an entry of its own (Definition 46), so equal handlers stay independent.
    log: list[str] = []

    async def handler(event: str) -> None:
        log.append(event)

    first = events.subscribe(PINGS, handler)
    events.subscribe(PINGS, handler)
    await events.publish(PINGS, "a")
    first()
    await events.publish(PINGS, "b")
    assert log == ["a", "a", "b"]

async def test_topics_with_the_same_name_are_different_topics(events: Events) -> None:
    log: list[str] = []

    async def handler(event: str) -> None:
        log.append(event)

    events.subscribe(Topic[str]("pings"), handler)
    await events.publish(PINGS, "a")
    assert log == []
