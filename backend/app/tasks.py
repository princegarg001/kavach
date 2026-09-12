"""
Fire-and-forget background tasks, done right.

`asyncio.create_task()` on its own is a trap: the event loop only holds a WEAK
reference to the task, so if nothing else references it, it can be garbage
collected mid-execution — silently, with no error, no log, nothing. Slower
coroutines (a network call to Twilio, a Supabase write) are exactly the ones
most likely to still be running when that happens.

`spawn()` keeps a strong reference in a module-level set until the task
finishes, and logs (rather than swallows) any exception it raised.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

logger = logging.getLogger("kavach.tasks")

_background_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine, *, name: str | None = None) -> asyncio.Task:
    """asyncio.create_task, but the task is guaranteed to run to completion."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(f"background task {t.get_name()} failed: {exc!r}", exc_info=exc)

    task.add_done_callback(_done)
    return task
