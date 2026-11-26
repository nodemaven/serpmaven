"""The cookie sets new exits start from: which one is in use, when a refused one
hands over, and how renewed sets are written for the next run."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable

from .errors import describe_error

#: Exits started from one cookie set and refused on their first query, in a
#: row, before the next set is used. A set refused once can be served again on
#: the next exit; two in a row usually means it is out of favour for a while.
DONOR_REFUSALS = 2
#: Cookie sets renewal keeps, each from a different served exit, so a refused
#: set has fresh ones to hand over to instead of one set overwritten in place.
DONOR_SLOTS = 4
#: How old a cookie set an earlier run saved may be and still start a run.
#: A set kept 24 hours on the same machine was served as often as a fresh one.
KEEP_HOURS = 24
#: Served queries on an exit between renewals of the cookie set, by default.
#: An exit started from a set gets results as good as one warmed up from scratch,
#: at a fraction of the traffic, so the first set is taken early: a short session,
#: such as an agent's, rarely reaches a large number before an exit is refused.
RENEW_EVERY = 5


class Donors:
    """The cookie sets new exits start from, used one at a time, moving on to
    the next after DONOR_REFUSALS first queries refused in a row. Once every
    set has been refused that way with nothing served in between, there is no
    set in use and new exits warm up again, until a served exit renews a set
    or a set is served. One instance serves every worker of a run (see `Pool`).

    Each slot carries a generation, bumped when a renewal replaces its set, so
    an exit started from the set a slot held before does not count against
    the set it holds now."""

    def __init__(
        self,
        sets: tuple[tuple[dict, ...], ...],
        out: str | None = None,
        slots: int = DONOR_SLOTS,
        files: tuple[str, ...] = (),
        build: str | None = None,
    ) -> None:
        self.sets = list(sets)
        self.out = out
        self.build = build
        self.slots = max(slots, 1)
        #: The file in `out` each slot writes to; see `Settings.donor_files`.
        self._files = list(files)[: len(self.sets)]
        self._generation = [0] * len(self.sets)
        #: The exit each slot was last renewed from; None for a set given up front.
        self._source: list[str | None] = [None] * len(self.sets)
        #: When each slot was last renewed, in renewals; the least recent one is
        #: overwritten once all slots are filled. Sets given up front come newest
        #: first, so they start ranked that way.
        self._renewed = list(range(len(self.sets), 0, -1))
        self._clock = len(self.sets)
        self._at = 0
        self._refused = 0
        #: Sets refused DONOR_REFUSALS times in a row since a set was last
        #: served or renewed; at len(sets) there is no set in use.
        self._spent = 0
        self._lock = threading.Lock()

    def current(self) -> tuple[int, int, tuple[dict, ...]] | None:
        """The slot in use, its generation and its set; None without sets or
        once every set has been refused."""
        with self._lock:
            if not self.sets or self._spent >= len(self.sets):
                return None
            return self._at, self._generation[self._at], self.sets[self._at]

    def served(self, index: int, generation: int) -> None:
        with self._lock:
            if (index, generation) == (self._at, self._generation[index]):
                self._refused = 0
                self._spent = 0

    def refused(self, index: int, generation: int, emit: Callable[[dict], None]) -> str:
        """Count a first query refused on a set: "stale" for an exit from a set
        already left behind or replaced, which is not counted; "spent" when
        this leaves no set in use, so new exits warm up again; else "counted"."""
        with self._lock:
            if self._spent >= len(self.sets):
                return "stale"  # already out of sets
            if (index, generation) != (self._at, self._generation[index]):
                return "stale"
            self._refused += 1
            if self._refused < DONOR_REFUSALS:
                return "counted"
            self._refused = 0
            self._spent += 1
            if self._spent >= len(self.sets):
                emit(
                    {
                        "event": "donors_spent",
                        "sets": len(self.sets),
                        "after_refusals": DONOR_REFUSALS,
                    }
                )
                return "spent"
            self._at = (self._at + 1) % len(self.sets)
            emit({"event": "donor_switched", "to": self._at, "after_refusals": DONOR_REFUSALS})
            return "counted"

    def renew(self, cookies: tuple[dict, ...], source: str, emit: Callable[[dict], None]) -> None:
        """Take fresher cookies from a served exit as the set in use. An exit
        renewing again replaces its own set; otherwise they go into a free
        slot, or over the oldest set once `slots` are filled. A refused set
        then hands over to the next slot, a recent set from another exit."""
        if not any(c.get("name") == "NID" and c.get("value") for c in cookies):
            # Not a set Google's trust rides on, and `load_cookies` would
            # refuse it as written out for the next run.
            return
        with self._lock:
            if source in self._source:
                at = self._source.index(source)
                self.sets[at] = cookies
            elif len(self.sets) < self.slots:
                self.sets.append(cookies)
                self._generation.append(0)
                self._source.append(None)
                self._renewed.append(0)
                at = len(self.sets) - 1
            else:
                at = min(range(len(self.sets)), key=self._renewed.__getitem__)
                self.sets[at] = cookies
            self._clock += 1
            self._renewed[at] = self._clock
            self._generation[at] += 1
            self._source[at] = source
            self._at = at
            self._refused = 0
            self._spent = 0
            # Written under the lock: workers renewing at once would otherwise
            # race on the slot's file.
            error = self._write(at, cookies) if self.out else None
        emit({"event": "donor_renewed", "set": at, "cookies": len(cookies)})
        if error:
            emit({"event": "donor_write_failed", "set": at, "error": error})

    def _free_name(self) -> str:
        """A `donor_<n>.json` name for a new slot. Not one of this run's, and
        not a file in `out` that may still be worth something - a set of
        another browser build, a broken or unreadable file: only a set older
        than KEEP_HOURS is written over."""
        taken = set(self._files)
        now = time.time()
        n = 0
        while True:
            name = f"donor_{n}.json"
            n += 1
            if name in taken:
                continue
            path = os.path.join(self.out, name)
            if not os.path.exists(path):
                return name
            if not os.path.isfile(path):
                continue  # a FIFO or device: opening it could block
            try:
                with open(path, encoding="utf-8") as fh:
                    written = json.load(fh).get("t")
                if (
                    isinstance(written, (int, float))
                    and not isinstance(written, bool)
                    and 0 <= now - written
                    and now - written > KEEP_HOURS * 3600
                ):
                    return name  # stale: nothing reads it any more
            except (OSError, ValueError, AttributeError, OverflowError):
                pass  # unreadable or broken: left for a person to look at

    def _write(self, at: int, cookies: tuple[dict, ...]) -> str | None:
        """Write a set for the next run's `donors`, readable by its owner only;
        the error, if the write failed - the set in memory is still used."""
        assert self.out is not None
        while len(self._files) <= at:
            self._files.append(self._free_name())
        path = os.path.join(self.out, self._files[at])
        tmp = None
        try:
            os.makedirs(self.out, exist_ok=True)
            # A new file of its own, created owner-only, then moved into place:
            # whatever sat at the path before, the result is owner-only.
            fd, tmp = tempfile.mkstemp(prefix=f".donor_{at}.", suffix=".tmp", dir=self.out)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                record = {"t": time.time(), "cookies": list(cookies)}
                if self.build is not None:
                    record["browser"] = self.build
                json.dump(record, fh)
            os.replace(tmp, path)
            tmp = None
        except OSError as exc:
            return describe_error(exc)
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.remove(tmp)
        return None
