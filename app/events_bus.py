"""Tiny in-process pub/sub so the SSE endpoint can push 'refresh' nudges
to every connected browser when the poller sees changes.

Queues are bounded: 'refresh' messages are idempotent (they just say "refetch"),
so if a browser stalls and its queue fills, we drop the oldest rather than grow
memory without limit. This keeps a slow/zombie client from affecting the rest.

NOTE: this bus is in-process. It only reaches browsers connected to THIS server
process, which is correct for the single-process deployment this app uses. Running
multiple workers would split clients across processes and break live updates — see
README "Running for a team".
"""
import asyncio

_subscribers: "set[asyncio.Queue]" = set()
_MAXSIZE = 100


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_MAXSIZE)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(message) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            # drop oldest, enqueue newest (refresh is idempotent)
            try:
                q.get_nowait()
                q.put_nowait(message)
            except Exception:
                pass
        except Exception:
            pass
