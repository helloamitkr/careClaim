from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN


async def test_published_event_reaches_subscriber():
    bus = EventBus()
    received = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("case.created", handler)
    event = Event(event_type="case.created", case=CASE_A_CLEAN)
    await bus.publish(event)

    assert received == [event]
    assert bus.history == [event]


async def test_unsubscribed_event_type_has_no_effect():
    bus = EventBus()
    event = Event(event_type="case.created", case=CASE_A_CLEAN)
    await bus.publish(event)  # no subscribers registered — should not raise
    assert bus.history == [event]


async def test_multiple_subscribers_all_receive_the_event():
    bus = EventBus()
    counts = {"a": 0, "b": 0}

    async def handler_a(event: Event) -> None:
        counts["a"] += 1

    async def handler_b(event: Event) -> None:
        counts["b"] += 1

    bus.subscribe("case.created", handler_a)
    bus.subscribe("case.created", handler_b)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    assert counts == {"a": 1, "b": 1}
