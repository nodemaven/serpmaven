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


class _Pages(_Recorder):
    """Two pages a query; `broken` queries fail on page 2."""

    def __init__(self, broken=()):
        super().__init__()
        self.broken = set(broken)

    def run(self, queries):
        for q in queries:
            self.asked.append(q)
            first = _record(q, page=1)
            first["pagination"] = {"next": "https://www.google.com/search?q=x&start=10"}
            yield first
            yield _record(q, "Error" if q in self.broken else "Success", page=2)


def test_only_a_query_answered_on_every_page_is_kept(tmp_path, monkeypatch):
    recorder = _Pages(broken={"tacos"})
    monkeypatch.setattr(cli, "_build", lambda args, proxy, **kw: (recorder, ""))
    argv = ["search", "pizza", "tacos", "--no-proxy", "--pages", "2", "--cache-ttl", "60"]
    argv += ["--cache-dir", str(tmp_path / "c")]
    cli.main([*argv, "-o", str(tmp_path / "a.jsonl")])
    recorder.asked.clear()
    recorder.broken.clear()
    cli.main([*argv, "-o", str(tmp_path / "b.jsonl")])
    assert recorder.asked == ["tacos"]  # pizza came from the cache, tacos had a failed page


def test_an_answer_that_does_not_start_at_page_1_is_not_kept(tmp_path):
    kept = []
    answers = cli._Answers(type("C", (), {"put": lambda self, q, r: kept.append(q)})())
    # Two workers on the same query: A's page 1, B's page 1, A's page 2, B's page 2.
    for page, complete in ((1, False), (1, False), (2, True), (2, True)):
        answers.add("pizza", _record("pizza", page=page), complete)
    assert kept == []  # two answers were coming in at once: neither is kept


def test_a_query_asked_again_after_its_answer_is_still_kept(tmp_path):
    kept = []
    answers = cli._Answers(type("C", (), {"put": lambda self, q, r: kept.append(q)})())
    for page, complete in ((1, False), (2, True), (1, False), (2, True)):
        answers.add("pizza", _record("pizza", page=page), complete)
    assert kept == ["pizza", "pizza"]


def test_the_proxy_is_part_of_the_cache_key_without_its_password(monkeypatch):
    parse = cli._parser().parse_args
    one = cli._proxy_identity(parse(["search", "q", "--proxy", "http://u-country-us:secret@g:1"]))
    two = cli._proxy_identity(parse(["search", "q", "--proxy", "http://u-country-de:secret@g:1"]))
    assert one != two and "secret" not in one and one.startswith("url:")


def test_a_malformed_proxy_url_is_refused_before_the_output_is_touched(tmp_path):
    out = tmp_path / "out.jsonl"
    out.write_text("kept\n", encoding="utf-8")
    code = cli.main(["search", "pizza", "--proxy", "not-a-url", "-o", str(out)])
    assert code != 0 and out.read_text(encoding="utf-8") == "kept\n"


def test_a_malformed_cache_file_falls_back_to_a_search(tmp_path):
    cache = ResultCache(tmp_path, 60, {})
    cache.put("pizza", [_record("pizza")])
    for f in tmp_path.iterdir():
        f.write_text(json.dumps({"saved_at": cache._clock(), "records": ["x"]}), encoding="utf-8")
    assert cache.get("pizza") is None


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
    assert "$0.00-0.00 at $0/GB" in cli._estimate(10, 1, 1, 0)


def test_blank_lines_are_not_in_the_estimate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_build", lambda args, proxy, **kw: (_Recorder(), ""))
    queries = tmp_path / "q.txt"
    queries.write_text("pizza\n\ntacos\n", encoding="utf-8")
    cli.main(["search", "-f", str(queries), "--no-proxy", "-o", str(tmp_path / "o.jsonl")])
    assert "estimate: 2 results pages" in capsys.readouterr().err
    assert cli._estimate(1, 1, 1, None).startswith("estimate: 1 results page,")
