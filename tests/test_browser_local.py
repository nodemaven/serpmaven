"""The real browser path against a page served from 127.0.0.1.

No network: a local server plays Google's front page and results page. It
checks the parts the fakes cannot - opening a results page, clearing a consent
wall and following "Next" - in an actual Patchright browser. Skipped when
Patchright or its browser is not installed.
"""

import fakegoogle
import pytest

patchright = pytest.importorskip("patchright.sync_api")

from urllib.parse import urlsplit  # noqa: E402

from serpmaven import browser  # noqa: E402
from serpmaven.classify import classify  # noqa: E402


@pytest.fixture
def local_google(monkeypatch):
    server, base = fakegoogle.start()
    monkeypatch.setattr(browser, "HOME_URL", base + "/")
    yield base
    server.shutdown()


def test_search_opens_the_results_page_and_paginates(local_google):
    try:
        session = browser.BrowserSession(None, headless=True).__enter__()
    except Exception as exc:  # browser binary missing on this machine
        pytest.skip(f"no Patchright browser: {exc}")
    try:
        first = session.search("swim lessons")
        assert urlsplit(first.url).path == "/search" and "q=swim+lessons" in first.url
        assert classify("https://www.google.com/search", first.html).served
        assert {"load", "ready", "read"} <= set(first.timings)

        second = session.search("pool near me")
        assert "q=pool+near+me" in second.url
        assert "swim" not in urlsplit(second.url).query

        nxt = session.next_page()
        assert nxt is not None and "start=10" in nxt.url
        assert {"load", "ready", "read"} <= set(nxt.timings)
    finally:
        session.close()


def test_blank_leaves_the_results_page_and_the_next_search_still_works(local_google):
    try:
        session = browser.BrowserSession(None, headless=True).__enter__()
    except Exception as exc:  # browser binary missing on this machine
        pytest.skip(f"no Patchright browser: {exc}")
    try:
        session.search("swim lessons")
        session.blank()
        assert session.page.url == "about:blank"
        after = session.search("pool near me")
        assert "q=pool+near+me" in after.url
        assert classify("https://www.google.com/search", after.html).served
    finally:
        session.close()


def test_a_search_clears_a_consent_wall_and_opens_the_results_again(local_google, monkeypatch):
    monkeypatch.setattr(fakegoogle, "CONSENT_ON_SEARCH", True)
    try:
        session = browser.BrowserSession(None, headless=True).__enter__()
    except Exception as exc:  # browser binary missing on this machine
        pytest.skip(f"no Patchright browser: {exc}")
    try:
        first = session.search("pool near me")
        assert first.consent_dismissed
        assert "q=pool+near+me" in first.url
        assert 'id="L2AGLb"' not in first.html  # the reopened page, not the walled one
        assert classify("https://www.google.com/search", first.html).served
        second = session.search("swim lessons")
        assert not second.consent_dismissed
    finally:
        session.close()


def test_donor_cookies_are_in_the_browser_before_the_first_page_and_survive_a_restart(local_google):
    # Google's cookies carry an expiry; a session cookie would not outlive the restart.
    import time

    try:
        browser.BrowserSession(None, headless=True).__enter__().close()
    except Exception as exc:  # browser binary missing on this machine
        pytest.skip(f"no Patchright browser: {exc}")
    donor = [
        {
            "name": "NID",
            "value": "donor",
            "domain": "127.0.0.1",
            "path": "/",
            "expires": time.time() + 86400,
        }
    ]
    session = browser.BrowserSession(None, headless=True, cookies=donor).__enter__()
    try:
        fakegoogle.COOKIES_SEEN.clear()
        session.visit(local_google + "/", 0)
        assert "NID=donor" in fakegoogle.COOKIES_SEEN[0]  # sent with the very first page
        session.suspend()
        session.resume()
        session.visit(local_google + "/", 0)
        assert "NID=donor" in fakegoogle.COOKIES_SEEN[-1]
    finally:
        session.close()
