"""Results kept between runs (--cache-ttl) and the estimate printed before a run."""

import json

from serpmaven import cli
from serpmaven.cache import ResultCache


def _record(query, status="Success", page=1, hl="en"):
    return {
        "search_metadata": {"status": status},
        "search_parameters": {"q": query, "page": page, "hl": hl},
        "organic_results": [{"position": 1, "title": query}],
    }


class _Recorder:
    stats = type("Stats", (), {"summary": lambda self, price: {}})()

    def __init__(self, fail=()):
        self.asked, self.fail = [], set(fail)

    def run(self, queries):
        for q in queries:
            self.asked.append(q)
            yield _record(q, "Error" if q in self.fail else "Success")


def _queries(path):
    return [json.loads(x)["search_parameters"]["q"] for x in path.read_text().splitlines()]


def test_the_cache_keeps_records_for_their_time_and_their_own_search(tmp_path):
    now = [1000.0]
    cache = ResultCache(tmp_path, 60, {"hl": "en", "pages": 1}, clock=lambda: now[0])
    assert cache.get("pizza") is None
    cache.put("pizza", [_record("pizza")])
    got = cache.get(" pizza ")
    assert got[0]["search_parameters"]["q"] == "pizza" and got[0]["search_metadata"]["cached"]
    other = ResultCache(tmp_path, 60, {"hl": "de", "pages": 1}, clock=lambda: now[0])
    assert other.get("pizza") is None  # another language is another search
    now[0] += 61
    assert cache.get("pizza") is None  # too old
    for f in tmp_path.iterdir():
        f.write_text("not json", encoding="utf-8")
    assert cache.get("pizza") is None


def test_a_second_run_answers_from_the_cache_without_the_browser(tmp_path, monkeypatch, capsys):
    recorder = _Recorder(fail={"tacos"})
    monkeypatch.setattr(cli, "_build", lambda args, proxy, **kw: (recorder, ""))
    base = ["search", "pizza", "tacos", "--no-proxy", "--cache-ttl", "60"]
    base += ["--cache-dir", str(tmp_path / "c")]
    first = tmp_path / "first.jsonl"
    assert cli.main([*base, "-o", str(first)]) == 4  # tacos failed
    assert recorder.asked == ["pizza", "tacos"]

    def no_browser(args, ask=True):
        raise AssertionError("the browser was checked for a cached query")

    second = tmp_path / "second.jsonl"
    recorder.asked.clear()
    recorder.fail.clear()
    assert cli.main([*base, "-o", str(second)]) == 0
    assert recorder.asked == ["tacos"]  # a failed query is not kept
    assert _queries(second) == ["pizza", "tacos"]
    assert "cache: 1 of 2 queries" in capsys.readouterr().err

    monkeypatch.setattr(cli, "_ready", no_browser)
    third = tmp_path / "third.jsonl"
    recorder.asked.clear()
    assert cli.main([*base, "-o", str(third)]) == 0
    assert recorder.asked == []
    lines = [json.loads(x) for x in third.read_text().splitlines()]
    assert [r["search_metadata"].get("cached") for r in lines] == [True, True]


def test_without_cache_ttl_nothing_is_kept(tmp_path, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(cli, "_build", lambda args, proxy, **kw: (recorder, ""))
    argv = ["search", "pizza", "--no-proxy", "--cache-dir", str(tmp_path / "c")]
    cli.main([*argv, "-o", str(tmp_path / "a.jsonl")])
    cli.main([*argv, "-o", str(tmp_path / "b.jsonl")])
    assert recorder.asked == ["pizza", "pizza"]
    assert not (tmp_path / "c").exists()


def test_a_run_prints_its_estimate_first(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_build", lambda args, proxy, **kw: (_Recorder(), ""))
    argv = ["search", "a", "b", "--no-proxy", "--pages", "2", "--price-per-gb", "3"]
    cli.main([*argv, "-o", str(tmp_path / "out.jsonl")])
    err = capsys.readouterr().err
    assert "estimate: 4 results pages, roughly 62-66 MB" in err and "at $3/GB" in err


def test_the_estimate_scales_with_pages_and_workers():
    one = cli._estimate(1000, 1, 1, None)
    assert "1000 results pages, roughly 560-1560 MB" in one and "150-240 min" in one
    assert "75-120 min" in cli._estimate(1000, 1, 2, None)
    assert cli._estimate(1, 1, 1, None).startswith("estimate: 1 results page,")
