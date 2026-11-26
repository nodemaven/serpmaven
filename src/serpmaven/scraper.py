"""The identity lifecycle: warm an exit, hold it while Google serves it, drop it
the moment Google refuses it.

The expensive event is finding an exit Google will serve. Once one is found it
usually keeps being served, so a served exit is worth a series of queries, and
a refused one is worth nothing - retrying it only burns the address further.

Traffic is counted by a loopback relay per identity (see `relay`);
`use_relay=False` hands the proxy to the browser directly instead.

With `spare` set, the next identities are warmed in the background while the
current one serves, so a retired exit is replaced without waiting for a
warm-up.
"""

from __future__ import annotations

import functools
import gzip
import random
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import browser, engines, quality
from . import warmup as ladder
from .classify import Verdict, classify

# Split out of this module; imported here so `serpmaven.scraper` keeps them.
from .donors import DONOR_REFUSALS, DONOR_SLOTS, KEEP_HOURS, RENEW_EVERY, Donors  # noqa: F401
from .errors import describe_error
from .hybrid import Headers, HybridSession
from .links import LinkResolver
from .output import build_record
from .parse import parse_serp
from .pending import PendingLinks, _fill_links, _unresolved  # noqa: F401
from .proxy import ProxySource, new_session_id
from .relay import Relay

MB = 1024 * 1024
MAX_SPARES = 3
TOP_HOSTS = 10
#: Records held back while their links resolve in the background; past this
#: the run waits for the oldest instead of opening more pages ahead of it.
#: Google holds some links for up to half a minute, so a page's links can take
#: longer than several pages do.
MAX_HELD = 8
#: Pages whose links resolve at once, each on its own thread: one page waiting
#: out a held link does not hold up the next page's links.
LINK_WORKERS = 4
#: What `prewarm()` searches for to find an exit Google serves: common queries
#: that look like anyone's first search of the day.
PREWARM_QUERIES = ("weather", "news", "translate", "time", "maps")


#: Chromium's own network failures: the tunnel, TLS or the connection broke.
_NETWORK_ERROR = re.compile(r"net::ERR_")

#: A browser that crashed or was closed under the search: a second search on it
#: fails the same way or hangs.
_BROWSER_GONE = re.compile(r"crashed|has been closed", re.IGNORECASE)


class ExitsRefused(RuntimeError):
    """Too many consecutive exits were refused or unreachable. Raised instead
    of carrying on, because on a shared, metered pool every further identity
    costs traffic and reputation and the evidence says it will fail too."""


class ProxyRefused(ExitsRefused):
    """The proxy itself refused: no traffic left (402) or wrong credentials
    (407). No other exit through the same account can do better."""


#: Proxy answers that hold for every exit of the account, not just this one.
_ACCOUNT_REFUSALS = (402, 407)


def _account_refusal(relay: Any) -> str | None:
    """The proxy's status line if it refused for the whole account, else None."""
    refusal = getattr(relay, "refusal", None)
    if not refusal:
        return None
    parts = refusal.split(" ", 2)
    if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) in _ACCOUNT_REFUSALS:
        return refusal
    return None


@dataclass
class Settings:
    engine: str = engines.DEFAULT_ENGINE
    warmup: str = ladder.DEFAULT
    pages: int = 1
    #: Each retirement throws away a warm-up worth tens of pages of traffic,
    #: and a served exit keeps being served for hundreds of queries; holds end
    #: on the exit dropping far more often than on Google refusing it.
    max_queries_per_exit: int = 300
    max_exits_per_query: int = 3
    breaker: int = 6
    gap_seconds: tuple[float, float] = (2.0, 5.0)
    dwell_seconds: tuple[float, float] = ladder.DWELL_SECONDS
    headless: bool = False
    channel: str | None = None
    timezone_id: str | None = None
    browser_args: tuple[str, ...] = ()
    use_relay: bool = True
    hl: str = "en"
    #: Country the results are for, as Google's `gl` (e.g. "us"); unset, Google
    #: picks it from the exit. Applies to results pages opened directly.
    gl: str | None = None
    #: Where the results are searched from, as a canonical place name
    #: ("Austin,Texas,United States"), sent as Google's `uule`.
    location: str | None = None
    #: Google's `tbs` filters as they are, e.g. "qdr:w" for the past week.
    tbs: str | None = None
    #: False sends `pws=0`: results without personalization.
    personalized: bool = True
    #: False sends `nfpr=1`: a misspelt query is searched as typed.
    autocorrect: bool = True
    #: Only documents from these countries (`cr`, e.g. "countryUS") or in these
    #: languages (`lr`, e.g. "lang_en").
    cr: str | None = None
    lr: str | None = None
    #: Directory to keep every page's HTML in, gzipped, for debugging a parse.
    #: The pages carry the exit's location, so keep them out of anything public.
    save_html: str | None = None
    #: Ask Google's /goto redirect for each result's destination, from a proxy
    #: session of its own; pages no longer carry the URLs themselves.
    resolve_links: bool = True
    #: Ask the links of the first that many organic results on each page only
    #: (0: none, None: all). Fewer links answer sooner and cost less traffic;
    #: a call to `search` or `start_search` can ask its own number.
    links: int | None = None
    #: Identities to warm in the background once the current one is served,
    #: so the next exit is ready when this one is retired. Each costs a
    #: warm-up's traffic whether it is used or not.
    spare: int = 0
    #: Wait between relaunching a warmed profile and its first search: a
    #: search the moment the browser starts is refused far more often.
    settle_seconds: tuple[float, float] = (40.0, 60.0)
    #: Results pages on one browser before it is closed and opened again on
    #: the same profile and exit. Google's renderer grows by 10-20 MB a page
    #: and a new tab reuses it, so only a new browser gives the memory back;
    #: at 50 a browser stays under about a gigabyte. It waits
    #: `settle_seconds` before searching again. 0 never restarts on a page
    #: count; the hybrid still restarts once per run, after its first served
    #: browser search, to read Chromium's request headers (see `hybrid`).
    restart_every: int = 50
    #: Fetch results pages over HTTP with the browser's cookies and headers,
    #: and use the browser only for Google's checks (see `hybrid`). Needs the
    #: relay; an engine that cannot do it ignores it.
    hybrid: bool = True
    #: Stop reading a results page that comes over HTTP at Google's footer and
    #: close the stream: what follows is scripts the parser does not read.
    cut_http_pages: bool = False
    #: Sets of Google cookies from browsers Google already serves, as
    #: Playwright cookie records. Every new exit starts with the current set
    #: instead of walking the warm-up: it carries Google's trust to a new IP,
    #: even one on another machine, and costs no traffic. After
    #: DONOR_REFUSALS exits in a row refused on their first query, the next set
    #: takes over; once every set has been refused that way, new exits warm up
    #: again until a renewal (`renew_donors_every`) brings a set back.
    donors: tuple[tuple[dict, ...], ...] = ()
    #: Every this many served queries on an exit, its Google cookies replace
    #: the cookie set in use, so the sets stay as fresh as the exits serving
    #: them and need no warm-up of their own. Without `donors` the first
    #: renewal turns warm-ups off: later exits start from it. 0 never renews.
    renew_donors_every: int = RENEW_EVERY
    #: Directory each renewed set is written to, as `donor_<n>.json`, for the
    #: next run's `donors`.
    donors_out: str | None = None
    #: The file in `donors_out` each set of `donors` came from, slot by slot: a
    #: renewal of slot n writes over `donor_files[n]`, so it replaces its own
    #: set's file and not another one. Slots past it take unused names.
    donor_files: tuple[str, ...] = ()
    #: The browser build this run uses (see `cli.browser_build`), written with
    #: each renewed set so a later run with another build leaves it alone.
    donor_build: str | None = None
    #: True asks Google's Web results only (`udm=14`) on every results page:
    #: the organic links without the AI Overview, "People also ask" or related
    #: searches, on a lighter page.
    lean: bool = False

    def __post_init__(self) -> None:  # noqa: C901
        engines.get(self.engine)
        ladder.pages(self.warmup)
        for name in ("pages", "max_queries_per_exit", "max_exits_per_query", "breaker"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if not 0 <= self.spare <= MAX_SPARES:
            raise ValueError(f"spare must be between 0 and {MAX_SPARES}")
        if self.restart_every < 0:
            raise ValueError("restart_every must be 0 or more")
        if self.links is not None and self.links < 0:
            raise ValueError("links must be 0 or more")
        if self.renew_donors_every < 0:
            raise ValueError("renew_donors_every must be 0 or more")
        for name in ("gap_seconds", "dwell_seconds", "settle_seconds"):
            low, high = getattr(self, name)
            if not 0 <= low <= high:
                raise ValueError(f"{name} must be a (low, high) pair with 0 <= low <= high")
        if self.location is not None:
            browser.uule(self.location)  # a name it cannot encode fails here, not mid-run


def filter_params(s: Settings) -> dict[str, str]:
    """What a record's `search_parameters` carry for these settings' location and
    filters (`location`, `tbs`, `pws`, `nfpr`, `cr`, `lr`, `udm`), as written there."""
    shown = {
        k: v
        for k, v in browser.url_params(
            s.location,
            s.tbs,
            personalized=s.personalized,
            autocorrect=s.autocorrect,
            cr=s.cr,
            lr=s.lr,
            lean=s.lean,
        ).items()
        if k != "uule"
    }
    if s.location:
        shown["location"] = s.location
    return shown


class Breaker:
    """Exits refused or unreachable in a row, across every worker sharing it.

    A served page anywhere resets the count. One worker meeting refusal after
    refusal while another keeps being served has drawn bad exits; the hour and
    the pool are fine, so the run carries on. It stops when nobody is served.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._count = 0
        self._lock = threading.Lock()

    def served(self) -> None:
        with self._lock:
            self._count = 0

    reset = served

    def failed(self) -> None:
        with self._lock:
            self._count += 1
            count = self._count
        if count >= self.limit:
            raise ExitsRefused(
                f"{count} exits in a row were refused or unreachable, none served in "
                "between. Stopping rather than spending more traffic: check the proxy, "
                "the machine (`serpmaven doctor`) and the hour, then retry."
            )


@dataclass
class Identity:
    session_id: str
    session: Any
    relay: Any
    warm_pages: int
    warm_delivered: int
    warm_bytes: int | None
    queries: int = 0
    consent_dismissed: bool = False
    warm_seconds: float = 0.0
    ready_at: float = 0.0
    #: Index of the `Settings.donors` set it started with instead of a warm-up,
    #: and that slot's generation then.
    donor: int | None = None
    donor_generation: int = 0
    #: Queries served here, counting each query once whatever its pages.
    served_queries: int = 0
    #: Bytes per host by the end of the warm-up, and inside served pages.
    warm_hosts: dict[str, int] = field(default_factory=dict)
    page_hosts: dict[str, int] = field(default_factory=dict)
    #: Results pages the browser rendered itself, not the hybrid's HTTP side;
    #: what makes a browser grow, so what `restart_every` counts.
    browser_pages: int = 0
    #: `browser_pages` when the current browser was opened.
    browser_start: int = 0
    #: The last served pages judged against their query, True where the
    #: titles have little to do with it.
    low_relevance: deque[bool] = field(default_factory=lambda: deque(maxlen=quality.POISON_WINDOW))
    #: When the last results page on this exit finished, for the gap.
    last_page_at: float = 0.0
    _window: dict[str, int] = field(default_factory=dict, repr=False)

    def bytes(self) -> int | None:
        return self.relay.total if self.relay is not None else None

    def hosts(self) -> dict[str, int]:
        return self.relay.hosts() if self.relay is not None else {}

    def open_window(self) -> int | None:
        """Start counting a page; returns what `bytes()` returns."""
        self._window = self.hosts()
        return self.bytes()

    def close_window(self) -> None:
        _add_hosts(self.page_hosts, self.hosts(), minus=self._window)

    def info(self, level: str, page_bytes: int | None = None) -> dict[str, Any]:
        return {
            "session": self.session_id,
            "queries_on_exit": self.queries,
            "warmup": level,
            "warm_pages_delivered": f"{self.warm_delivered}/{self.warm_pages}",
            "warm_bytes": self.warm_bytes,
            "bytes": page_bytes,
            "consent_dismissed": self.consent_dismissed,
        }


@dataclass
class Stats:
    """What a run cost, counted across every identity including refused ones."""

    identities: int = 0
    pages_served: int = 0
    queries_failed: int = 0
    exits_refused: int = 0
    exits_unreachable: int = 0
    exits_poisoned: int = 0
    pages_http: int = 0
    bytes_total: int = 0
    bytes_warm: int = 0
    links_resolved: int = 0
    links_failed: int = 0
    counted: bool = True
    bytes_by_host: dict[str, int] = field(default_factory=dict)
    _open: list = field(default_factory=list, repr=False)
    #: The link resolver while it is open, so a long-lived scraper (serve,
    #: MCP) reports its traffic before `close()` folds it in.
    _links: Any = field(default=None, repr=False)

    def summary(self, price_per_gb: float | None = None) -> dict[str, Any]:
        total = self.bytes_total + sum(i.bytes() or 0 for i in self._open)
        by_host = dict(self.bytes_by_host)
        for identity in self._open:
            _add_hosts(by_host, identity.hosts())
        if self._links is not None:
            total += self._links.bytes
            _add_hosts(by_host, {f"{h} (links)": n for h, n in self._links.hosts().items()})
        out: dict[str, Any] = {
            "identities": self.identities,
            "pages_served": self.pages_served,
            "queries_failed": self.queries_failed,
            "exits_refused": self.exits_refused,
            "exits_unreachable": self.exits_unreachable,
            "exits_poisoned": self.exits_poisoned,
            "pages_http": self.pages_http,
            "links_resolved": self.links_resolved,
            "links_failed": self.links_failed,
        }
        if self.counted:
            out["traffic_mb"] = round(total / MB, 2)
            out["warmup_mb"] = round(self.bytes_warm / MB, 2)
            if self.pages_served:
                per_page = total / MB / self.pages_served
                out["mb_per_served_page"] = round(per_page, 2)
                if price_per_gb is not None:
                    out["cost_per_1000_pages"] = round(per_page / 1024 * price_per_gb * 1000, 2)
            if by_host:
                top = sorted(by_host.items(), key=lambda item: -item[1])[:TOP_HOSTS]
                out["traffic_by_host_mb"] = {host: round(n / MB, 2) for host, n in top}
        return out


def _by_size(hosts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(hosts.items(), key=lambda item: -item[1]))


def _add_hosts(into: dict[str, int], more: dict[str, int], minus: dict[str, int] | None = None):
    for host, n in more.items():
        n -= (minus or {}).get(host, 0)
        if n:
            into[host] = into.get(host, 0) + n


class Pinned:
    """A browser session whose every call runs on one thread of its own.

    Playwright's sync API is bound to the thread that started it. A spare is
    warmed on a background thread and searched later from the caller's, so each
    spare gets a thread that owns its browser from launch to close.
    """

    def __init__(self, make: Callable[[], Any]) -> None:
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="serpmaven-identity")
        try:
            self.session = self._call(make)
        except BaseException:
            self._thread.shutdown(wait=False)
            raise

    def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        return self._thread.submit(fn, *args).result()

    def __enter__(self) -> Pinned:
        self._call(self.session.__enter__)
        return self

    def visit(self, url: str, dwell_seconds: float) -> bool:
        return self._call(self.session.visit, url, dwell_seconds)

    def search(self, query: str, **locale: str) -> Any:
        return self._call(lambda: self.session.search(query, **locale))

    def next_page(self) -> Any:
        return self._call(self.session.next_page)

    def needs_restart(self) -> bool:
        return self._call(getattr(self.session, "needs_restart", lambda: False))

    def link_client(self) -> Any:
        return self._call(getattr(self.session, "link_client", lambda: None))

    def google_cookie_records(self) -> tuple[dict, ...]:
        return self._call(self.session.google_cookie_records)

    @property
    def http_off(self) -> bool:
        return bool(getattr(self.session, "http_off", False))

    def blank(self) -> None:
        self._call(self.session.blank)

    def suspend(self) -> str:
        return self._call(self.session.suspend)

    def resume(self) -> None:
        self._call(self.session.resume)

    def close(self) -> None:
        try:
            self._call(self.session.close)
        finally:
            self._thread.shutdown(wait=False)


class Scraper:
    """Run queries through a pool of browser identities.

    `session_factory`, `relay_factory` and `sleep` are injectable so the
    lifecycle can be tested without a browser, a network or a clock.
    """

    def __init__(
        self,
        proxy: ProxySource | None = None,
        settings: Settings | None = None,
        *,
        session_factory: Callable[..., Any] | None = None,
        relay_factory: Callable[..., Any] | None = Relay,
        resolver_factory: Callable[..., Any] | None = LinkResolver,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.proxy = proxy
        self.settings = settings or Settings()
        self._factory = session_factory or engines.get(self.settings.engine)
        self._relay_factory = relay_factory
        self._resolver_factory = resolver_factory
        self._resolver = None
        self._link_pool: ThreadPoolExecutor | None = None
        self._link_jobs: dict[int, Future] = {}
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._emit = on_event or (lambda event: None)
        self._identity: Identity | None = None
        #: Upkeep a `search(settle=False)` left for `settle()`: (identity, more).
        self._unsettled: tuple[Identity, bool] | None = None
        #: The hl and gl of the query being run, where `search()` was given them.
        self._locale: dict[str, str] = {}
        #: Organic results whose links the query being run asks for (None: all).
        self._link_limit: int | None = None
        self._spares: deque[Future] = deque()
        self._warmer: ThreadPoolExecutor | None = None
        self._cancel = threading.Event()
        self._breaker = Breaker(self.settings.breaker)
        self._donors = Donors(
            self.settings.donors,
            self.settings.donors_out,
            files=self.settings.donor_files,
            build=self.settings.donor_build,
        )
        self._headers = Headers()
        self._saved = 0
        self.stats = Stats()

    def share(
        self,
        breaker: Breaker,
        headers: Headers | None = None,
        donors: Donors | None = None,
    ) -> None:
        """Use a breaker, the hybrid's request headers and the cookie sets that
        other scrapers use too (see `Pool`)."""
        self._breaker = breaker
        if headers is not None:
            self._headers = headers
        if donors is not None:
            self._donors = donors

    # -- public --------------------------------------------------------------

    def run(self, queries: Iterable[str]) -> Iterator[dict[str, Any]]:
        """Yield one record per results page, in order, then close the last
        identity.

        A record's links resolve on a thread of their own while the browser
        goes on to the next query; the record is yielded once they are in. A
        run that stops on an error (the breaker, a proxy refusal) still yields
        the pages it already served before the error is raised.
        """
        held: deque[dict[str, Any]] = deque()
        try:
            for record in self._records(queries):
                held.append(record)
                while held and (len(held) > MAX_HELD or self._links_ready(held[0])):
                    yield self._finish(held.popleft())
        except Exception:
            while held:
                yield self._finish(held.popleft())
            raise
        else:
            while held:
                yield self._finish(held.popleft())
        finally:
            self.close()

    def _records(self, queries: Iterable[str]) -> Iterator[dict[str, Any]]:
        pending = (q.strip() for q in queries)
        pending = (q for q in pending if q)
        query = next(pending, None)
        while query is not None:
            following = next(pending, None)
            # No spare is warmed for a run that has nothing left to ask.
            yield from self._query(query, more=following is not None)
            query = following

    def search(
        self,
        query: str,
        settle: bool = True,
        hl: str | None = None,
        gl: str | None = None,
        links: int | None = None,
    ) -> list[dict[str, Any]]:
        """One query, keeping the identity open for the next call (serve, MCP).

        With `settle=False` the upkeep a served query leaves behind - a scheduled
        restart, retiring a capped exit - waits for `settle()`, so a server can
        answer first; a due renewal of the cookie set is done before the answer.
        A restart closes and reopens the browser, which can take longer than the
        search itself.

        `hl` and `gl` override the settings for this query. `links` asks the
        links of the first that many organic results only (0: none).
        """
        pending = self.start_search(query, settle=settle, hl=hl, gl=gl, links=links)
        return [self._finish(r) for r in pending.records]

    def start_search(
        self,
        query: str,
        settle: bool = True,
        hl: str | None = None,
        gl: str | None = None,
        links: int | None = None,
    ) -> PendingLinks:
        """As `search`, but returns once the results pages are read: their
        links go on resolving on the link threads, so the browser is free for
        the next query while a caller waits for them (`PendingLinks.wait`)."""
        if links is not None and links < 0:
            raise ValueError("links must be 0 or more")
        if links is None:
            links = self.settings.links
        self.settle()
        # Jobs a caller of an earlier start_search waited for, or did not.
        self._link_jobs = {k: job for k, job in self._link_jobs.items() if not job.done()}
        self._hold_back()
        self._locale = {k: v for k, v in (("hl", hl), ("gl", gl)) if v}
        self._link_limit = links
        records: list[dict[str, Any]] = []
        try:
            for record in self._query(query.strip(), settle=settle):
                records.append(record)
                if len(records) < self.settings.pages:
                    self._hold_back()  # before the next page is read
        finally:
            self._locale = {}
            self._link_limit = None
        jobs = {
            index: self._link_jobs[id(record)]
            for index, record in enumerate(records)
            if id(record) in self._link_jobs
        }
        return PendingLinks(records, jobs, getattr(self._resolver, "lock", None), links)

    def _hold_back(self) -> None:
        """As `run` holds at most MAX_HELD records: read no more pages while
        that many are still waiting for their links."""
        unfinished = [job for job in self._link_jobs.values() if not job.done()]
        while len(unfinished) >= MAX_HELD:
            wait_futures(unfinished, return_when=FIRST_COMPLETED)
            unfinished = [job for job in unfinished if not job.done()]

    def _hl(self) -> str:
        return self._locale.get("hl") or self.settings.hl

    def _gl(self) -> str | None:
        return self._locale.get("gl") or self.settings.gl

    @property
    def _params(self) -> dict[str, str]:
        s = self.settings
        return browser.url_params(
            s.location,
            s.tbs,
            personalized=s.personalized,
            autocorrect=s.autocorrect,
            cr=s.cr,
            lr=s.lr,
            lean=s.lean,
        )

    def _record_params(self) -> dict[str, str]:
        return filter_params(self.settings)

    def settle(self) -> None:
        """Run the upkeep the last `search(settle=False)` left, if any."""
        pending, self._unsettled = self._unsettled, None
        if pending is not None:
            self._upkeep(*pending)

    def prewarm(self) -> None:
        """Find an exit Google serves before the first real query.

        A warm-up alone does not tell: a third or more of fresh exits are refused
        on their first search. So one common query runs on fresh exits until a
        results page comes back, and a server's first caller does not pay for a
        refused exit and a second warm-up. Raises `ExitsRefused` when the
        breaker trips first.

        The query is one page with no link resolution: its records are thrown
        away, so neither more pages nor /goto requests buy anything. Its upkeep
        is done here, so the first real query does not pay for it. With one
        query per exit (`max_queries_per_exit=1`) that upkeep retires the
        served exit at once and nothing can be held for the first caller.
        """
        s = self.settings
        query = self._rng.choice(PREWARM_QUERIES)
        pages, links = s.pages, s.resolve_links
        s.pages, s.resolve_links = 1, False
        try:
            while True:
                records = [self._finish(r) for r in self._query(query)]
                if records and records[0]["search_metadata"]["status"] == "Success":
                    return
        finally:
            s.pages, s.resolve_links = pages, links

    def reset_breaker(self) -> None:
        self._breaker.reset()

    def close(self) -> None:
        self._unsettled = None
        if self._identity is not None:
            self._retire("run finished")
        self._discard_spares()
        if self._link_pool is not None:
            self._link_pool.shutdown(wait=True)
            self._link_pool = None
            self._link_jobs.clear()
        if self._resolver is not None:
            self._resolver.close()
            self.stats.bytes_total += self._resolver.bytes
            _add_hosts(
                self.stats.bytes_by_host,
                {f"{host} (links)": n for host, n in self._resolver.hosts().items()},
            )
            self.stats.links_resolved += self._resolver.resolved
            self.stats.links_failed += self._resolver.failed
            self._resolver = None
            self.stats._links = None

    # -- one query -----------------------------------------------------------

    def _query(
        self, query: str, more: bool = True, settle: bool = True
    ) -> Iterator[dict[str, Any]]:
        s = self.settings
        last_verdict: Verdict | None = None
        last_error: str | None = None
        for _ in range(s.max_exits_per_query):
            identity = self._current_identity()
            if identity is None:
                last_error = "exit unreachable: no warm-up page arrived"
                continue
            self._rest_before(identity)
            started, before = time.monotonic(), identity.open_window()
            try:
                fetched = self._search(identity, query)
            except Exception as exc:
                last_error = describe_error(exc)
                self._check_proxy(identity)
                self._retire(f"error: {describe_error(exc)}")
                self._count_failure()
                continue
            identity.queries += 1
            identity.consent_dismissed = identity.consent_dismissed or fetched.consent_dismissed
            verdict = classify(fetched.url, fetched.html, fetched.status)
            self._event("query", identity, q=query, page=1, verdict=verdict.kind)
            saved = self._keep(fetched, query, 1, verdict)
            if verdict.served:
                record = self._served(query, 1, fetched, verdict, identity, started, before, saved)
                if self._poisoned(identity, query, record):
                    self._drop_poisoned(record)
                    last_verdict, last_error = None, "results unrelated to the query"
                    continue
                self._count_served(identity, more)
                yield record
                yield from self._more_pages(query, identity)
                self._after_query(identity, more, settle)
                return
            last_verdict, last_error = verdict, None
            self._drop_refused(identity, verdict)
        self.stats.queries_failed += 1
        reason = last_verdict.reason if last_verdict else "unknown"
        yield build_record(
            query,
            page=1,
            verdict=last_verdict,
            url=None,
            elapsed=0.0,
            hl=self._hl(),
            gl=self._gl(),
            params=self._record_params(),
            exit_info={"session": None},
            error=last_error or f"refused on {s.max_exits_per_query} exits: {reason}",
        )

    def _rest_before(self, identity: Identity) -> None:
        """Wait out the gap between pages on a held exit. The gap is time since
        the last page, not a wait added to it: a server whose callers pause on
        their own has waited already."""
        if not identity.queries:
            return
        idle = time.monotonic() - identity.last_page_at
        rest = self._rng.uniform(*self.settings.gap_seconds) - idle
        if rest > 0:
            self._sleep(rest)

    def _drop_poisoned(self, record: dict[str, Any]) -> None:
        """Results for other queries than the one asked: Google's answer to some
        automated traffic. The page is not returned and the exit is retired."""
        self._finish(record)
        self.stats.pages_served -= 1
        if record["search_metadata"]["fetched_with"] == "http":
            self.stats.pages_http -= 1
        self.stats.exits_poisoned += 1
        self._retire("poisoned")
        self._count_failure()

    def _count_served(self, identity: Identity, more: bool) -> None:
        """A query's first page was served on `identity`."""
        self._breaker.served()
        if identity.donor is not None and identity.queries == 1:
            self._donors.served(identity.donor, identity.donor_generation)
        identity.served_queries += 1
        every = self.settings.renew_donors_every
        if every and identity.served_queries % every == 0:
            self._renew_donor(identity)
        if more:
            self._top_up()

    def _after_query(self, identity: Identity, more: bool, settle: bool) -> None:
        """What a served query leaves: the exit retired when its HTTP side gave
        up, or the upkeep, now or when the caller settles."""
        if self._donors.current() is not None and getattr(identity.session, "http_off", False):
            # Every further page here would come from the browser, at several
            # times the traffic; a new exit from a cookie set costs next to
            # nothing and gets the HTTP side back.
            self._retire("http side off")
            return
        if settle:
            self._upkeep(identity, more)
        else:
            self._unsettled = (identity, more)

    def _drop_refused(self, identity: Identity, verdict: Verdict) -> None:
        """Google refused `identity` (or it answered with no page): retire it and
        count the refusal against its cookie set or the breaker."""
        donor = None
        if identity.donor is not None and identity.queries == 1 and verdict.refused:
            donor = self._donors.refused(identity.donor, identity.donor_generation, self._emit)
        self.stats.exits_refused += verdict.refused
        self._retire(verdict.kind)
        if donor == "spent":
            # Every cookie set refused: new exits warm up again, and the
            # breaker counts the warm-ups from zero.
            self._breaker.reset()
        elif donor is None:
            # An exit from a cookie set costs next to nothing, and the sets'
            # own limit (DONOR_REFUSALS each) bounds how many are tried, as do
            # the workers for exits already started from a set another worker
            # has since left ("stale"); counting them too would trip the
            # breaker before every set had been tried and the warm-ups reached.
            self._count_failure()

    def _search(self, identity: Identity, query: str):
        """Search on this identity, once more if the browser fails.

        A browser error is not Google's answer: a click that timed out under an
        overlay says nothing about the exit. Retiring on it throws away a
        warm-up that costs far more than one more page.

        A network error is the exception: the proxy or the exit failed, and a
        second page through it fails the same way after another full timeout.
        So are running out of memory and a crashed or closed browser: a second
        search on it fails again or hangs, so the exit is retired instead.
        """
        try:
            return identity.session.search(query, **self._locale)
        except Exception as exc:
            if (
                isinstance(exc, MemoryError)
                or _NETWORK_ERROR.search(str(exc))
                or _BROWSER_GONE.search(str(exc))
            ):
                raise
            self._event("search_retry", identity, error=describe_error(exc))
            return identity.session.search(query, **self._locale)

    def _renew_donor(self, identity: Identity) -> None:
        try:
            cookies = identity.session.google_cookie_records()
        except Exception as exc:  # the browser is mid-restart or gone; next time
            self._event("donor_renew_failed", identity, error=describe_error(exc))
            return
        self._donors.renew(tuple(cookies), identity.session_id, self._emit)

    def _upkeep(self, identity: Identity, more: bool) -> None:
        """What a served query leaves to do once its pages are out."""
        s = self.settings
        capped = identity.queries >= s.max_queries_per_exit
        if more and not capped and self._identity is identity and self._wants_restart(identity):
            self._restart(identity)
        elif (
            s.restart_every
            and more
            and not capped
            and self._identity is identity
            and identity.browser_pages - identity.browser_start >= s.restart_every
        ):
            self._restart(identity)
        if self._identity is identity and capped:
            self._retire("query cap reached")

    def _more_pages(self, query: str, identity: Identity) -> Iterator[dict[str, Any]]:
        for page in range(2, self.settings.pages + 1):
            self._sleep(self._rng.uniform(*self.settings.gap_seconds))
            started, before = time.monotonic(), identity.open_window()
            try:
                fetched = identity.session.next_page()
            except Exception as exc:
                self._retire(f"error: {describe_error(exc)}")
                yield self._failed(query, page, None, describe_error(exc), identity)
                return
            if fetched is None:
                return  # Google has no further page for this query.
            identity.queries += 1
            verdict = classify(fetched.url, fetched.html, fetched.status)
            self._event("query", identity, q=query, page=page, verdict=verdict.kind)
            saved = self._keep(fetched, query, page, verdict)
            if not verdict.served:
                self.stats.exits_refused += verdict.refused
                record = self._failed(query, page, verdict, verdict.reason, identity)
                self._retire(verdict.kind)
                yield record
                return
            record = self._served(query, page, fetched, verdict, identity, started, before, saved)
            if self._poisoned(identity, query, record):
                # Later pages are judged like the first: a poisoned page 2
                # ends the query's pages and the exit, and is not returned.
                self._finish(record)
                self.stats.pages_served -= 1
                if record["search_metadata"]["fetched_with"] == "http":
                    self.stats.pages_http -= 1
                self.stats.exits_poisoned += 1
                self._retire("poisoned")
                yield self._failed(query, page, None, "results unrelated to the query", identity)
                return
            yield record

    def _links(self):
        if not self.settings.resolve_links or self._resolver_factory is None:
            return None
        if self._resolver is None:
            upstream = self.proxy.for_session(new_session_id()) if self.proxy is not None else None
            relay = self._relay_factory if self.settings.use_relay else None
            self._resolver = self._resolver_factory(upstream, relay_factory=relay)
            self.stats._links = self._resolver
        return self._resolver

    def _keep(self, fetched, query: str, page: int, verdict: Verdict) -> str | None:
        if not self.settings.save_html:
            return None
        folder = Path(self.settings.save_html)
        folder.mkdir(parents=True, exist_ok=True)
        self._saved += 1
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:40] or "query"
        path = folder / f"{self._saved:04d}_{verdict.kind}_{slug}_p{page}.html.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(fetched.html)
        return str(path)

    def _served(
        self, query, page, fetched, verdict, identity, started, before, saved=None
    ) -> dict[str, Any]:
        after = identity.bytes()
        identity.close_window()
        identity.last_page_at = time.monotonic()
        page_bytes = after - before if after is not None and before is not None else None
        self.stats.pages_served += 1
        timings = dict(getattr(fetched, "timings", None) or {})
        mark = time.monotonic()
        parsed = parse_serp(fetched.html)
        timings["parse"] = round(time.monotonic() - mark, 2)
        if "http" in timings:
            self.stats.pages_http += 1
        else:
            identity.browser_pages += 1
        record = build_record(
            query,
            page=page,
            verdict=verdict,
            url=fetched.url,
            hl=self._hl(),
            gl=self._gl(),
            params=self._record_params(),
            elapsed=time.monotonic() - started,
            exit_info=identity.info(self.settings.warmup, page_bytes),
            parsed=parsed,
            timings=timings,
        )
        record["search_metadata"]["fetched_with"] = "http" if "http" in timings else "browser"
        if saved:
            record["search_metadata"]["raw_html_file"] = saved
        resolver = self._links()
        limit = self._link_limit if self._link_limit is not None else self.settings.links
        if resolver is not None and limit != 0:
            if self._link_pool is None:
                self._link_pool = ThreadPoolExecutor(
                    LINK_WORKERS, thread_name_prefix="serpmaven-links"
                )
            make = getattr(identity.session, "link_client", None)
            client = make() if make else None
            failed = functools.partial(
                self._event, "link_lookup_failed", identity, query=query, page=page
            )
            self._link_jobs[id(record)] = self._link_pool.submit(
                _fill_links, resolver, record, fetched.html, client, limit, failed
            )
        return record

    def _links_ready(self, record: dict[str, Any]) -> bool:
        job = self._link_jobs.get(id(record))
        return job is None or job.done()

    def _finish(self, record: dict[str, Any]) -> dict[str, Any]:
        """The record once its links are in; a link left unresolved stays None."""
        job = self._link_jobs.pop(id(record), None)
        if job is not None:
            try:
                job.result()
            except Exception:
                pass  # the resolver counts its own failures; the page is still served
        return record

    def _failed(self, query, page, verdict, error, identity) -> dict[str, Any]:
        return build_record(
            query,
            page=page,
            verdict=verdict,
            url=None,
            elapsed=0.0,
            hl=self._hl(),
            gl=self._gl(),
            params=self._record_params(),
            exit_info=identity.info(self.settings.warmup),
            error=error,
        )

    # -- identities ----------------------------------------------------------

    def _current_identity(self) -> Identity | None:
        if self._identity is None:
            if self._spares:
                self._identity = self._take_spare()
            else:
                self._identity = self._mint()
        return self._identity

    def _wants_restart(self, identity: Identity) -> bool:
        """The hybrid reads Chromium's request headers from a NetLog, which is
        complete only once the browser is closed."""
        check = getattr(identity.session, "needs_restart", None)
        return bool(check and check())

    def _poisoned(self, identity: Identity, query: str, record: dict[str, Any]) -> bool:
        titles = [r.get("title") or "" for r in record.get("organic_results") or []]
        unrelated = quality.unrelated(query, titles)
        if unrelated is not None:
            identity.low_relevance.append(unrelated)
        return sum(identity.low_relevance) >= quality.POISON_HITS

    def _mint(self) -> Identity | None:
        return self._adopt(self._warm(pinned=False))

    def _warm(self, pinned: bool) -> Identity:  # noqa: C901
        """Launch a browser on a new sticky session and walk the warm-up ladder.

        Touches no scraper state beyond the relay decision, so it can run on a
        background thread; the identity is counted when it is adopted.
        """
        s = self.settings
        session_id = new_session_id()
        upstream = self.proxy.for_session(session_id) if self.proxy is not None else None
        relay, browser_proxy = self._relay_for(upstream)
        started = time.monotonic()
        picked = self._donors.current()
        donor = {"cookies": picked[2]} if picked else {}
        # Donor cookies stand in for the warm-up; no settle either, a first
        # search right after launch is served as often with them.
        pages = () if donor else ladder.pages(s.warmup)
        self._emit(
            {
                "event": "warming",
                "session": session_id,
                "pages": len(pages),
                "donor": bool(donor),
                "spare": pinned,
            }
        )

        def make() -> Any:
            return self._hybrid(
                self._factory(
                    browser_proxy,
                    headless=s.headless,
                    channel=s.channel,
                    timezone_id=s.timezone_id,
                    browser_args=s.browser_args,
                    hl=s.hl,
                    gl=s.gl,
                    params=self._params,
                    **donor,
                ),
                relay,
                browser_proxy,
            )

        try:
            session = Pinned(make) if pinned else make()
        except Exception:
            if relay is not None:
                relay.stop()
            raise
        try:
            session.__enter__()
        except Exception:
            if pinned:
                session.close()
            if relay is not None:
                relay.stop()
            raise
        delivered = 0
        for url in pages:
            if pinned and self._cancel.is_set():
                break  # the run is closing; stop paying for a spare nobody will use
            delivered += bool(session.visit(url, self._rng.uniform(*s.dwell_seconds)))
            if _account_refusal(relay):
                break  # every further page is refused too, each after a full timeout
        if delivered:
            # A warm-up page can navigate itself after the dwell (Trends does),
            # which would interrupt the first search; leave it first.
            try:
                session.blank()
            except Exception as exc:
                self._emit(
                    {"event": "blank_failed", "session": session_id, "error": describe_error(exc)}
                )
        warm_bytes = relay.total if relay is not None else None
        return Identity(
            session_id,
            session,
            relay,
            len(pages),
            delivered,
            warm_bytes,
            warm_seconds=round(time.monotonic() - started, 1),
            ready_at=time.monotonic(),
            warm_hosts=relay.hosts() if relay is not None else {},
            donor=picked[0] if picked else None,
            donor_generation=picked[1] if picked else 0,
        )

    def _count_in(self, identity: Identity, **spare: Any) -> None:
        self.stats.identities += 1
        self.stats.bytes_warm += identity.warm_bytes or 0
        self.stats._open.append(identity)
        self._event(
            "minted",
            identity,
            warm_delivered=identity.warm_delivered,
            warm_pages=identity.warm_pages,
            warm_bytes=identity.warm_bytes,
            warm_hosts=_by_size(identity.warm_hosts),
            seconds=identity.warm_seconds,
            **({"donor": identity.donor} if identity.donor is not None else {}),
            **spare,
        )

    def _adopt(self, identity: Identity, **spare: Any) -> Identity | None:
        self._count_in(identity, **spare)
        if identity.warm_pages and not identity.warm_delivered:
            # Nothing arrived: the proxy is down, the credentials are wrong or
            # the exit is dead. Not Google's verdict, but just as final.
            self._check_proxy(identity)
            self._identity = identity
            self.stats.exits_unreachable += 1
            self._retire("unreachable")
            self._count_failure()
            return None
        return identity

    def _hybrid(self, session: Any, relay: Any, browser_proxy: dict[str, str] | None) -> Any:
        """The session, driven by the hybrid when the engine allows it. The HTTP
        side goes through the identity's relay, so it uses the same exit and its
        traffic is counted with the browser's. With the hybrid turned off the
        session still wraps the browser, but only to resolve links on this exit:
        every results page comes from the browser."""
        if not (getattr(session, "hybrid_capable", False) and relay):
            return session
        return HybridSession(
            session,
            browser_proxy["server"],
            self._headers,
            self._emit,
            pages_over_http=self.settings.hybrid,
            cut_pages=self.settings.cut_http_pages,
        )

    def _relay_for(self, upstream: dict[str, str] | None):
        """Decide whether this identity goes through the counting relay."""
        needs = getattr(self._factory, "needs_relay", False)
        relayable = upstream is None or upstream["server"].startswith("http://")
        if self._relay_factory is None or not (self.settings.use_relay or needs):
            self.stats.counted = False
            return None, upstream
        if not relayable:
            if needs:
                raise ValueError(
                    f"the {self.settings.engine} engine needs the relay, which "
                    "speaks HTTP to the upstream proxy; use an http:// proxy URL"
                )
            self.stats.counted = False
            return None, upstream
        relay = self._relay_factory(upstream)
        return relay, {"server": relay.start()}

    # -- spares --------------------------------------------------------------

    def _top_up(self) -> None:
        """Start warming spares in the background, up to `settings.spare`."""
        if not self.settings.spare:
            return
        if self._warmer is None:
            self._warmer = ThreadPoolExecutor(
                max_workers=self.settings.spare, thread_name_prefix="serpmaven-warm"
            )
        while len(self._spares) < self.settings.spare:
            self._spares.append(self._warmer.submit(self._warm, True))

    def _take_spare(self) -> Identity | None:
        """The oldest spare, waiting for its warm-up if it is still running."""
        future = self._spares.popleft()
        asked = time.monotonic()
        try:
            identity = future.result()
        except Exception as exc:
            # A spare that failed in the background is not a reason to stop
            # the run; the identity is made here instead, as without spares.
            self._emit({"event": "spare_failed", "error": describe_error(exc)})
            return self._mint()
        now = time.monotonic()
        return self._adopt(
            identity,
            spare=True,
            waited=round(now - asked, 1),
            idle=round(max(0.0, now - identity.ready_at), 1),
        )

    def _discard_spares(self) -> None:
        """Close spares nobody used. Their warm-up was paid for, so it is counted."""
        self._cancel.set()
        while self._spares:
            try:
                identity = self._spares.popleft().result()
            except Exception:
                continue
            self._count_in(identity, spare=True)
            self._close_identity(identity, "spare unused")
        if self._warmer is not None:
            self._warmer.shutdown(wait=True)
            self._warmer = None
        self._cancel = threading.Event()

    def _restart(self, identity: Identity) -> None:
        """Close the browser and open it again on the same profile and exit."""
        identity.browser_start = identity.browser_pages
        try:
            identity.session.suspend()
            identity.session.resume()
        except Exception as exc:
            self._event("restart_failed", identity, error=describe_error(exc))
            self._retire(f"error: {describe_error(exc)}")
            return
        self._event("restart", identity, queries=identity.queries)
        self._sleep(self._rng.uniform(*self.settings.settle_seconds))

    def _retire(self, reason: str) -> None:
        identity, self._identity = self._identity, None
        if identity is not None:
            self._close_identity(identity, reason)

    def _close_identity(self, identity: Identity, reason: str) -> None:
        # Links of its last pages are asked on its exit, which closes with it.
        for job in list(self._link_jobs.values()):
            try:
                job.result()
            except Exception:
                pass
        try:
            identity.session.close()
        finally:
            if identity.relay is not None:
                identity.relay.stop()
                self.stats.bytes_total += identity.relay.total
                _add_hosts(self.stats.bytes_by_host, identity.relay.hosts())
            if identity in self.stats._open:
                self.stats._open.remove(identity)
            self._event(
                "retired",
                identity,
                reason=reason,
                queries=identity.queries,
                bytes=identity.bytes(),
                hosts=_by_size(identity.hosts()),
                page_hosts=_by_size(identity.page_hosts),
            )

    def _check_proxy(self, identity: Identity) -> None:
        """Stop the run if the proxy refused for the whole account.

        The browser reports any refused tunnel as `ERR_TUNNEL_CONNECTION_FAILED`;
        the relay kept the proxy's own status line. 402 and 407 will be the
        answer for every exit, so drawing more exits only adds failures.
        """
        refusal = _account_refusal(identity.relay)
        if refusal:
            if self._identity is identity:
                self._retire(f"proxy: {refusal}")
            else:
                self._close_identity(identity, f"proxy: {refusal}")
            raise ProxyRefused(
                f"the proxy refused: {refusal}. Check the account's traffic and credentials."
            )

    def _event(self, kind: str, identity: Identity, **fields) -> None:
        self._emit({"event": kind, "session": identity.session_id, **fields})

    def _count_failure(self) -> None:
        self._breaker.failed()
