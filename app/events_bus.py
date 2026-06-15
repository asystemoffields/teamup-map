"""Tiny in-process pub/sub so the SSE endpoint can push 'refresh' nudges
to every connected browser when the poller sees changes."""
import asyncio

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(message) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(message)
        except Exception:
            pass
