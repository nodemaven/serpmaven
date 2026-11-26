"""Result links resolving after a page is read: the job that fills them, and the
records a caller holds while they come in."""

from __future__ import annotations

import contextlib
import copy
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import wait as wait_futures
from typing import Any

from .errors import describe_error


def _fill_links(
    resolver,
    record: dict[str, Any],
    page: str = "",
    client=None,
    limit: int | None = None,
    failed: Callable[..., None] | None = None,
) -> None:
    """A record's links: on the exit its page came from, through each result's
    ping URL, then from the resolver's own exit for whatever is left. With
    `limit`, only the first that many organic results are asked. A step that
    raises is reported to `failed(step=, error=)`; the links it did not reach
    stay unresolved."""
    mark = time.monotonic()
    results = record.get("organic_results") or []
    if limit is not None:
        results = results[:limit]

    def report(step: str, exc: Exception) -> None:
        if failed is not None:
            failed(step=step, error=describe_error(exc))

    if client is not None:
        try:
            resolver.fill_from_pings(results, page, client.get)
        except Exception as exc:  # whatever is left goes to the /goto resolver below
            report("pings", exc)
        finally:
            client.close()
    try:
        resolver.fill(results)
    except Exception as exc:
        report("goto", exc)
    with getattr(resolver, "lock", None) or contextlib.nullcontext():
        timings = record["search_metadata"].setdefault("timings", {})
        timings["links"] = round(time.monotonic() - mark, 2)


class PendingLinks:
    """A query's records, read, while their links go on resolving on the link
    threads.

    `wait()` blocks until every link is in or a deadline passes; `snapshot()`
    is the records as they stand, safe to serialize while links are still
    being written into them: a copy, made under the resolver's lock, with
    `search_metadata.links_pending` on each record whose links are not all in.
    `limit` is the number of organic results whose links were asked (None: all).
    """

    def __init__(
        self,
        records: list[dict[str, Any]],
        jobs: dict[int, Future] | None = None,
        lock: Any = None,
        limit: int | None = None,
    ) -> None:
        self.records = records
        #: Index into `records` -> the job filling that record's links.
        self._jobs = jobs or {}
        self.lock = lock or threading.Lock()
        self.limit = limit

    def done(self) -> bool:
        return all(job.done() for job in self._jobs.values())

    def wait(self, timeout: float | None = None) -> bool:
        """True once every link is in, False if `timeout` seconds passed first."""
        if not self._jobs:
            return True
        finished, _ = wait_futures(list(self._jobs.values()), timeout=timeout)
        return len(finished) == len(self._jobs)

    def snapshot(self) -> list[dict[str, Any]]:
        if self.done():
            return self.records
        with self.lock:
            out = copy.deepcopy(self.records)
            for index, job in self._jobs.items():
                if not job.done():
                    pending = _unresolved(out[index], self.limit)
                    out[index]["search_metadata"]["links_pending"] = pending
        return out


def _unresolved(record: dict[str, Any], limit: int | None = None) -> int:
    """Results among the first `limit` (all with None) still waiting for a link."""
    results = (record.get("organic_results") or [])[:limit]
    return sum(
        1 for r in results if r.get("link") is None and "/goto?" in (r.get("redirect_link") or "")
    )
