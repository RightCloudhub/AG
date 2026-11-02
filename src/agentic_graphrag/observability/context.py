"""Scoped query identity and context propagation across execution boundaries."""

from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from contextvars import copy_context
from typing import cast

from agentic_graphrag.observability.logging_setup import (
    query_id_var,
    tenant_id_var,
    user_id_var,
)


def submit_with_context[**P, T](
    pool: Executor, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs
) -> Future[T]:
    """Give each worker its own copy of logging and OpenTelemetry context."""
    context = copy_context()
    return pool.submit(lambda: context.run(fn, *args, **kwargs))


@contextmanager
def query_context(*, query_id: str, tenant_id: str, user_id: str) -> Iterator[None]:
    """Bind one query's identity and restore the caller's context on every exit."""
    tokens = [
        (variable, variable.set(value))
        for variable, value in (
            (query_id_var, query_id),
            (tenant_id_var, tenant_id),
            (user_id_var, user_id),
        )
    ]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def iterate_with_context[T](iterator: Iterator[T]) -> Iterator[T]:
    """Keep a stream's context across yields without leaking it into its consumer."""
    context = copy_context()
    exhausted = object()
    try:
        while (item := context.run(next, iterator, exhausted)) is not exhausted:
            yield cast(T, item)
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            context.run(close)
