import io
import json
import time

import pages
import pytest

from serpmaven import cli
from serpmaven.classify import classify
from serpmaven.doctor import FAIL, PASS, WARN, assess
from serpmaven.output import build_record, open_writer
from serpmaven.parse import parse_serp


def record():
    page = pages.results_page()
    return build_record(
        "swimming",
        page=1,
        verdict=classify("https://www.google.com/search", page),
        url="https://www.google.com/search?q=swimming",
        elapsed=1.234,
        exit_info={"session": "abc"},
        parsed=parse_serp(page),
    )


def test_record_is_serp_api_shaped():
    r = record()
    assert r["search_metadata"]["status"] == "Success"
    assert r["search_parameters"] == {
        "engine": "google",
        "q": "swimming",
        "page": 1,
        "hl": "en",
        "device": "desktop",
    }
    for key in ("organic_results", "ads", "related_questions", "related_searches", "pagination"):
        assert key in r
    assert "error" not in r


def test_jsonl_and_csv_writers():
    out = io.StringIO()
    writer = open_writer("jsonl", out)
    writer.write(record())
    assert json.loads(out.getvalue())["search_parameters"]["q"] == "swimming"

    out = io.StringIO()
    open_writer("csv", out).write(record())
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("query,page,position,title,link")
    assert len(lines) == 4


def test_parse_command_reads_a_saved_page(tmp_path, capsys):
    saved = tmp_path / "serp.html"
    saved.write_text(pages.results_page(), encoding="utf-8")
    assert cli.main(["parse", str(saved)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["page_verdict"] == "ok"
    assert len(out["organic_results"]) == 3


def test_parse_command_refuses_to_parse_a_refusal(tmp_path, capsys):
    saved = tmp_path / "sorry.html"
    saved.write_text(pages.SORRY_PAGE, encoding="utf-8")
    assert cli.main(["parse", str(saved)]) == 4
    assert "organic_results" not in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    "argv",
    [
        ["search"],
        ["search", "q"],
        ["search", "q", "--proxy", "http://u:p@h:1", "--country", "us"],
        ["search", "q", "--no-proxy", "--spare", "9"],
    ],
)
def test_search_argument_errors(argv, capsys, monkeypatch):
    monkeypatch.delenv("SERPMAVEN_PROXY", raising=False)
    monkeypatch.delenv("GSERP_PROXY", raising=False)
    monkeypatch.delenv("GBS_PROXY", raising=False)
    assert cli.main(argv) == 1
    assert capsys.readouterr().err.startswith("error:")


def test_doctor_assessment():
    windows = {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/151.0.0.0",
        "platform": "Win32",
        "webdriver": False,
        "webgl_renderer": "ANGLE (Intel, UHD Graphics, D3D11)",
        "hardware_concurrency": 12,
        "screen": "1920x1080",
        "color_depth": 24,
    }
    assert {c.status for c in assess(windows)} == {PASS}

    linux_server = dict(
        windows,
        platform="Linux x86_64",
        webgl_renderer=None,
        hardware_concurrency=2,
        screen="1280x1024",
    )
    by_name = {c.name: c.status for c in assess(linux_server)}
    assert by_name["WebGL"] == WARN
    assert by_name["CPU cores"] == WARN
    # Linux runs as well as Windows; the platform is reported, not flagged.
    assert by_name["platform"] == PASS
    software = dict(linux_server, webgl_renderer="ANGLE (Google, SwiftShader Device)")
    assert {c.name: c.status for c in assess(software)}["WebGL"] == PASS

    headless = dict(windows, user_agent="Mozilla/5.0 HeadlessChrome/151.0.0.0")
    assert {c.name: c.status for c in assess(headless)}["user agent"] == FAIL


def test_a_query_file_saved_with_a_byte_order_mark(tmp_path, monkeypatch):
    # Windows tools (PowerShell 5 `Out-File -Encoding utf8`, older Notepad)
    # write a BOM; it must not end up in the first query.
    asked = []

    class Recorder:
        stats = type("Stats", (), {"summary": lambda self, price: {}})()

        def run(self, queries):
            asked.extend(queries)
            return iter(())

    monkeypatch.setattr(cli, "_build", lambda args, proxy, **kw: (Recorder(), ""))
    path = tmp_path / "q.txt"
    path.write_bytes("\ufeffbest pizza\nkimchi\n".encode("utf-8"))
    assert cli.main(["search", "-f", str(path), "--no-proxy"]) == 0
    assert asked == ["best pizza", "kimchi"]


def test_verbose_events_carry_a_timestamp(capsys):
    args = cli._parser().parse_args(["search", "q", "--no-proxy", "-v"])
    scraper, _ = cli._build(args, None)
    scraper._emit({"event": "retired", "session": "abc"})
    event = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert event["event"] == "retired" and isinstance(event["ts"], float)


def test_workers_share_one_demo_sub_user(monkeypatch):
    made, closed, given = [], [], []

    class Demo:
        sticky = True

        def __init__(self, **kw):
            made.append(self)

        def close(self, keep=False):
            closed.append(keep)

    class FakePool:
        stats = type("Stats", (), {"summary": lambda self, price: {}})()

        def __init__(self, make, workers):
            self.make, self.workers = make, workers

        def run(self, queries):
            for i in range(self.workers):
                self.make(i)
            return iter(())

    def build(args, proxy, **kw):
        given.append(proxy)
        return None, ""

    monkeypatch.setattr(cli, "NodeMavenDemoSource", Demo)
    monkeypatch.setattr(cli, "Pool", FakePool)
    monkeypatch.setattr(cli, "_build", build)
    assert cli.main(["search", "q", "--nodemaven-demo", "--workers", "3"]) == 0
    assert len(made) == 1 and given == made * 3 and closed == [False]


def test_a_bad_output_path_still_deletes_the_demo_sub_user(tmp_path, monkeypatch):
    closed = []

    class Demo:
        sticky = True

        def __init__(self, **kw):
            pass

        def close(self, keep=False):
            closed.append(keep)

    monkeypatch.setattr(cli, "NodeMavenDemoSource", Demo)
    monkeypatch.setattr(cli, "_build", lambda args, proxy, **kw: (None, ""))
    missing = tmp_path / "no-such-dir" / "out.jsonl"
    with pytest.raises(OSError):
        cli.main(["search", "q", "--nodemaven-demo", "-o", str(missing)])
    assert closed == [False]


def test_copies_are_on_by_default_and_can_be_turned_off():
    parser = cli._parser()
    assert parser.parse_args(["search", "q", "--no-proxy"]).clone == 10
    assert parser.parse_args(["search", "q", "--no-proxy", "--clone", "0"]).clone == 0


def test_cookie_sets_are_renewed_by_default_and_zero_brings_copies_back():
    parse = cli._parser().parse_args
    settings = cli._build(parse(["search", "q", "--no-proxy"]), None)[0].settings
    assert settings.renew_donors_every == 5 and settings.clones == 0
    args = parse(["search", "q", "--no-proxy", "--renew-cookies", "0"])
    settings = cli._build(args, None)[0].settings
    assert settings.renew_donors_every == 0 and settings.clones == 10


def test_links_resolves_all_by_default_five_for_mcp_and_refuses_a_negative():
    parse = cli._parser().parse_args

    def links(argv):
        return cli._build(parse(argv), None)[0].settings.links

    assert links(["search", "q", "--no-proxy"]) is None
    assert links(["serve", "--no-proxy"]) is None
    assert links(["mcp", "--no-proxy"]) == 5
    assert links(["search", "q", "--no-proxy", "--links", "0"]) == 0
    assert links(["mcp", "--no-proxy", "--links", "10"]) == 10
    with pytest.raises(ValueError, match="--links"):
        links(["search", "q", "--no-proxy", "--links", "-1"])


def test_load_cookies_takes_a_donor_file_and_keeps_google_only(tmp_path):
    import json

    from serpmaven.browser import load_cookies

    f = tmp_path / "donor.json"
    f.write_text(
        json.dumps(
            {
                "t": 1,
                "cookies": [
                    {
                        "name": "NID",
                        "value": "v",
                        "domain": ".google.com",
                        "path": "/",
                        "partitionKey": "x",
                    },
                    {"name": "_ga", "value": "w", "domain": ".example.com", "path": "/"},
                    {
                        "name": "DV",
                        "value": "old",
                        "domain": "www.google.com",
                        "path": "/",
                        "expires": 1.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    assert load_cookies(str(f)) == (
        {"name": "NID", "value": "v", "domain": ".google.com", "path": "/"},
    )
    f.write_text(
        json.dumps([{"name": "x", "value": "1", "domain": ".example.com"}]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no Google cookies"):
        load_cookies(str(f))
    f.write_text(json.dumps([{"value": "1", "domain": ".google.com"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="name, value and domain"):
        load_cookies(str(f))


def test_load_cookies_refuses_malformed_fields_before_a_browser_starts(tmp_path):
    import json

    from serpmaven.browser import load_cookies

    f = tmp_path / "donor.json"
    nid = {"name": "NID", "value": "v", "domain": ".google.com", "path": "/"}
    for bad in (
        {"expires": None},
        {"expires": "soon"},
        {"expires": True},
        {"expires": float("nan")},
        {"expires": float("inf")},
        {"httpOnly": "yes"},
        {"secure": 1},
        {"sameSite": "lax"},
        {"sameSite": "None"},
        {"sameSite": "None", "secure": False},
    ):
        f.write_text(json.dumps([{**nid, **bad}]), encoding="utf-8")
        with pytest.raises(ValueError, match="malformed"):
            load_cookies(str(f))
    # An expired cookie is dropped whatever its other fields say.
    stale = {**nid, "name": "DV", "expires": 1.0, "sameSite": "lax"}
    f.write_text(json.dumps([nid, stale]), encoding="utf-8")
    assert load_cookies(str(f)) == (nid,)
    good = {**nid, "expires": -1, "httpOnly": True, "secure": True, "sameSite": "None"}
    f.write_text(json.dumps([good]), encoding="utf-8")
    assert load_cookies(str(f)) == (good,)
    # A cookie that is dropped anyway is not judged.
    other = {"name": "x", "value": "1", "domain": ".example.com", "expires": None}
    f.write_text(json.dumps([nid, other]), encoding="utf-8")
    assert load_cookies(str(f)) == (nid,)


def test_load_cookies_drops_lookalike_domains_and_needs_a_live_nid(tmp_path):
    import json

    from serpmaven.browser import load_cookies

    f = tmp_path / "donor.json"
    nid = {"name": "NID", "value": "v", "domain": ".google.com", "path": "/"}
    # Only google.com is ever asked; other domains, Google's or not, are dropped.
    others = [
        {"name": "NID", "value": "x", "domain": d, "path": "/"}
        for d in (".notgoogle.com", ".google.com.evil.net", ".google.co.de", ".google.de")
    ]
    f.write_text(json.dumps([nid, *others]), encoding="utf-8")
    assert load_cookies(str(f)) == (nid,)
    empty = {"name": "NID", "value": "", "domain": ".google.com", "path": "/"}
    f.write_text(json.dumps([empty]), encoding="utf-8")
    with pytest.raises(ValueError, match="no live NID"):
        load_cookies(str(f))
    f.write_text(json.dumps([{"name": "NID", "value": "v", "domain": 7}]), encoding="utf-8")
    with pytest.raises(ValueError, match="name, value and domain"):
        load_cookies(str(f))
    f.write_text(
        json.dumps([{"name": "AEC", "value": "a", "domain": ".google.com", "path": "/"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no live NID"):
        load_cookies(str(f))


def test_cookies_is_repeatable_and_leaves_the_queries_alone():
    parser = cli._parser()
    args = parser.parse_args(["search", "--cookies", "a.json", "best pizza", "--no-proxy"])
    assert args.cookies == ["a.json"] and args.queries == ["best pizza"]
    args = parser.parse_args(["search", "q", "--cookies", "a.json", "--cookies", "b.json"])
    assert args.cookies == ["a.json", "b.json"]
    with pytest.raises(SystemExit):
        parser.parse_args(["search", "q", "--renew-cookies", "-1"])


def _set(name_value, written):
    """A kept set as this run's browser build would have written it."""
    cookie = {"name": "NID", "value": name_value, "domain": ".google.com", "path": "/"}
    build = cli.browser_build(cli._parser().parse_args(["search", "q", "--no-proxy"]))
    return {"t": written, "browser": build, "cookies": [cookie]}


def test_a_run_starts_from_the_sets_an_earlier_run_kept_and_keeps_its_own(tmp_path):
    import time

    from serpmaven import config

    folder = config.cookies_dir()
    folder.mkdir(parents=True)
    now = time.time()
    (folder / "donor_0.json").write_text(json.dumps(_set("older", now - 3600)))
    (folder / "donor_1.json").write_text(json.dumps(_set("newer", now - 60)))
    (folder / "donor_2.json").write_text(json.dumps(_set("stale", now - 25 * 3600)))
    (folder / "donor_3.json").write_text("not json")
    (folder / "notes.json").write_text(json.dumps(_set("foreign", now)))
    parse = cli._parser().parse_args
    settings = cli._build(parse(["search", "q", "--no-proxy"]), None)[0].settings
    assert [s[0]["value"] for s in settings.donors] == ["newer", "older"]
    assert settings.donors_out == str(folder)
    assert settings.clones == 0


def test_a_renewal_writes_back_to_the_file_its_set_came_from(tmp_path):
    from serpmaven.scraper import Donors

    def keep(name, hours_ago, value):
        cookies = [{"name": "NID", "value": value, "domain": ".google.com", "path": "/"}]
        record = {"t": time.time() - hours_ago * 3600, "cookies": cookies}
        (tmp_path / name).write_text(json.dumps(record), encoding="utf-8")

    keep("donor_0.json", 20, "old")
    keep("donor_1.json", 1, "fresh")
    kept = cli.kept_cookie_sets(str(tmp_path))
    assert [(c[0]["value"], name) for c, name in kept] == [
        ("fresh", "donor_1.json"),
        ("old", "donor_0.json"),
    ]
    donors = Donors(
        tuple(c for c, _ in kept), str(tmp_path), slots=2, files=tuple(n for _, n in kept)
    )
    renewed = ({"name": "NID", "value": "new", "domain": ".google.com", "path": "/"},)
    donors.renew(renewed, "exit0", lambda e: None)  # all slots full: the oldest set goes

    def on_disk(name):
        return json.loads((tmp_path / name).read_text(encoding="utf-8"))["cookies"][0]["value"]

    assert on_disk("donor_0.json") == "new" and on_disk("donor_1.json") == "fresh"


def test_sets_another_browser_build_made_are_not_used(tmp_path):
    from serpmaven.scraper import Donors

    nid = {"name": "NID", "value": "v", "domain": ".google.com", "path": "/"}
    Donors((), str(tmp_path), build="patchright/bundled/1.0").renew((nid,), "e", lambda e: None)
    saved = json.loads((tmp_path / "donor_0.json").read_text(encoding="utf-8"))
    assert saved["browser"] == "patchright/bundled/1.0"
    assert len(cli.kept_cookie_sets(str(tmp_path), build="patchright/bundled/1.0")) == 1
    assert cli.kept_cookie_sets(str(tmp_path), build="patchright/chrome/1.0") == ()
    assert cli.kept_cookie_sets(str(tmp_path), build="patchright/bundled/2.0") == ()


def test_a_new_slot_leaves_other_builds_and_broken_files_alone(tmp_path):
    from serpmaven.scraper import Donors

    nid = {"name": "NID", "value": "new", "domain": ".google.com", "path": "/"}
    other = {"t": time.time(), "browser": "patchright/chrome/1.0", "cookies": [nid]}
    stale = {"t": time.time() - 48 * 3600, "browser": "patchright/bundled/1.0", "cookies": [nid]}
    (tmp_path / "donor_0.json").write_text(json.dumps(other), encoding="utf-8")
    (tmp_path / "donor_1.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "donor_2.json").write_text(json.dumps(stale), encoding="utf-8")
    Donors((), str(tmp_path), build="patchright/bundled/1.0").renew((nid,), "e", lambda e: None)
    assert json.loads((tmp_path / "donor_0.json").read_text())["browser"] == "patchright/chrome/1.0"
    assert (tmp_path / "donor_1.json").read_text() == "{broken"
    assert (
        json.loads((tmp_path / "donor_2.json").read_text())["browser"] == "patchright/bundled/1.0"
    )
    assert json.loads((tmp_path / "donor_2.json").read_text())["t"] > time.time() - 60


def test_the_browser_build_names_engine_channel_and_package_version():
    from importlib.metadata import version

    parse = cli._parser().parse_args
    made_by = version("patchright")
    bundled = parse(["search", "q", "--no-proxy"])
    chrome = parse(["search", "q", "--no-proxy", "--channel", "chrome"])
    assert cli.browser_build(bundled) == f"patchright/bundled/{made_by}"
    assert cli.browser_build(chrome) == f"patchright/chrome/{made_by}"
    # and the run's settings carry it, so renewed sets are written with it
    assert cli._build(bundled, None)[0].settings.donor_build == f"patchright/bundled/{made_by}"
    assert cli._build(chrome, None)[0].settings.donor_build == f"patchright/chrome/{made_by}"


def test_kept_sets_are_left_alone_when_turned_off_or_given_explicitly(tmp_path):
    import time

    from serpmaven import config

    folder = config.cookies_dir()
    folder.mkdir(parents=True)
    (folder / "donor_0.json").write_text(json.dumps(_set("kept", time.time())))
    given = tmp_path / "given.json"
    given.write_text(json.dumps(_set("given", time.time())))
    parse = cli._parser().parse_args

    def settings(argv):
        return cli._build(parse(["search", "q", "--no-proxy", *argv]), None)[0].settings

    off = settings(["--no-keep-cookies"])
    assert off.donors == () and off.donors_out is None
    no_renewal = settings(["--renew-cookies", "0"])
    assert no_renewal.donors == () and no_renewal.donors_out is None
    explicit = settings(["--cookies", str(given)])
    assert [s[0]["value"] for s in explicit.donors] == ["given"]
    assert explicit.donors_out is None
    out = tmp_path / "out"
    out.mkdir()
    (out / "donor_0.json").write_text(json.dumps(_set("from-out", time.time())))
    chosen = settings(["--cookies-out", str(out)])
    assert [s[0]["value"] for s in chosen.donors] == ["from-out"]
    assert chosen.donors_out == str(out)


def test_every_worker_of_a_run_starts_from_the_same_kept_sets():
    import time

    from serpmaven import config

    folder = config.cookies_dir()
    folder.mkdir(parents=True)
    path = folder / "donor_0.json"
    path.write_text(json.dumps(_set("first", time.time())))
    args = cli._parser().parse_args(["search", "q", "--no-proxy"])
    first = cli._settings(args, worker=0).donors
    path.write_text(json.dumps(_set("second", time.time())))
    assert cli._settings(args, worker=1).donors == first
