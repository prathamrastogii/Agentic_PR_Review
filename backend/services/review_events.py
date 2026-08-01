"""In-process event bus for live review streaming (SSE).

Uses a context variable so graph nodes can emit without threading callbacks
through every function signature.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
EmitFn = Callable[[dict[str, Any]], Awaitable[None]]

_emitter: contextvars.ContextVar[EmitFn | None] = contextvars.ContextVar(
    "review_emitter", default=None
)


async def emit_review_event(event: dict[str, Any]) -> None:
    emitter = _emitter.get()
    if emitter is not None:
        await emitter(event)


@contextlib.asynccontextmanager
async def review_event_scope(emitter: EmitFn | None) -> AsyncIterator[None]:
    if emitter is None:
        yield
        return
    token = _emitter.set(emitter)
    try:
        yield
    finally:
        _emitter.reset(token)


async def emit_budget(used: int, maximum: int) -> None:
    await emit_review_event({"type": "budget", "used": used, "max": maximum})
