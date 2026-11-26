<!-- mcp-name: io.github.nodemaven/serpmaven -->
<div align="center">

<img src="https://raw.githubusercontent.com/nodemaven/serpmaven/main/assets/banner.png" alt="SERP Maven">

<h1><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/nodemaven/serpmaven/main/assets/raven-animated-dark.svg"><img src="https://raw.githubusercontent.com/nodemaven/serpmaven/main/assets/raven-animated.svg" alt="" height="48" align="absmiddle"></picture> SERP Maven</h1>

<p><b>Self-hosted SERP API alternative: search results from a real Chrome browser and your own residential proxy.</b></p>

<p><code>pip install serpmaven</code>. Formerly <code>gserp</code> and <code>google-browser-scraper</code>: both now install serpmaven, and their old commands, imports and <code>GSERP_*</code> / <code>GBS_*</code> variables keep working.</p>

[![pypi](https://img.shields.io/pypi/v/serpmaven?style=flat-square&cacheSeconds=3600)](https://pypi.org/project/serpmaven/)
[![python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://pypi.org/project/serpmaven/)
[![tests](https://img.shields.io/github/actions/workflow/status/nodemaven/serpmaven/ci.yml?branch=main&style=flat-square&label=tests)](https://github.com/nodemaven/serpmaven/actions/workflows/ci.yml)
[![license](https://img.shields.io/pypi/l/serpmaven?style=flat-square&cacheSeconds=3600)](https://opensource.org/licenses/MIT)
[![docker](https://img.shields.io/badge/docker-ghcr.io-2496ED?style=flat-square&logo=docker&logoColor=white)](https://github.com/nodemaven/serpmaven/pkgs/container/serpmaven)
[![2 GB free with code GOOGLENM](https://img.shields.io/badge/%F0%9F%8E%81%202%20GB%20free-code%20GOOGLENM-1153FC?style=flat-square)](https://dashboard.nodemaven.com/accounts/signup/?utm_source=github&utm_medium=readme&utm_campaign=google_browser_scraper&utm_content=promo_badge)

<a href="https://devhunt.org/tool/google-search-scraper"><img src="https://devhunt.org/badge/google-search-scraper.svg" alt="SERP Maven on DevHunt" height="44"></a>

[Quickstart](#quickstart) · [Output](#what-you-get) · [Poisoned results](#poisoned-results-protection) · [HTTP API](#run-it-as-an-api) · [MCP](#use-it-from-an-ai-agent-mcp) · [Docker](#docker) · [How it works](#how-it-works) · [Options](#options)

</div>

<br>

<p align="center">
  <img src="https://raw.githubusercontent.com/nodemaven/serpmaven/main/assets/demo.gif" alt="A real Chrome window showing Google results next to the JSON the scraper produced for them" width="900">
</p>

<div align="center">

| ✅ **100%** | 📉 **0.4 MB** | ⚡ **1.4 s** |
|:---:|:---:|:---:|
| success rate | per search | per search on a warm proxy IP |

<sub>Latest version benchmarks (v0.1)</sub>

</div>

Scrape Google search results (SERP) with a real Chrome browser and your own
residential proxy, and get clean JSON back. Use it as a CLI, a Python library,
a local HTTP API, or an MCP server that gives AI agents live Google search.

Plain HTTP is no longer a reliable way to scrape Google, and a fresh browser on a
fresh proxy IP is refused quickly. So the scraper warms its first sticky proxy
session with ordinary browsing and starts later ones from the cookies Google
already accepts, lets the browser pass Google's checks, and then fetches results
pages directly over HTTPS with the browser's own cookies. Captchas and
blocks are reported as such, never as "no results".

## Features

<table>
<tr>
<td width="50%" valign="top">

⚡ **Hybrid mode**<br>
Results pages over HTTPS between Google's checks: a fraction of a browser's traffic and time. The browser takes over by itself when needed.

</td>
<td width="50%" valign="top">

🛡️ **Poisoned results protection**<br>
Pages of results for some other query are caught: the proxy IP serving them is dropped within a few pages and the query is asked again on a fresh one.

</td>
</tr>
<tr>
<td valign="top">

🌐 **Real browser**<br>
Real Chrome via Patchright, warmed up once and then carrying the cookies Google already trusts to every new identity, so Google's checks pass.

</td>
<td valign="top">

🧩 **SERP API-style JSON**<br>
`organic_results`, `ads`, `related_questions`, `related_searches`, `pagination`, `ai_overview`, `local_results`.

</td>
</tr>
<tr>
<td valign="top">

🔌 **Bring your own proxy**<br>
Any residential proxy with sticky sessions, built-in [NodeMaven](https://nodemaven.com/?utm_source=github&utm_medium=readme&utm_campaign=google_browser_scraper&utm_content=features) support.

</td>
<td valign="top">

🚦 **Honest failures**<br>
Captchas and blocks come back labeled, not as empty results.

</td>
</tr>
<tr>
<td valign="top">

🤖 **For AI agents**<br>
An MCP server that gives agents live Google search, with the AI Overview and map places.

</td>
<td valign="top">

🐳 **Run it your way**<br>
CLI, Python library, HTTP API, MCP server or Docker image. Traffic and cost per 1000 pages in every run summary.

</td>
</tr>
</table>

## Use cases

- SEO rank tracking and SERP monitoring for your own keywords
- Keyword and competitor research from live Google results
- Web search for AI agents and LLM apps through MCP
- A self-hosted alternative to paid Google search APIs when you already pay for proxies

## Install

Python 3.10 or newer. On Windows:

```powershell
pip install serpmaven
```

On macOS and Linux the system Python refuses `pip install` outside a virtual environment, so
make one first:

```bash
python3 -m venv serpmaven-env
source serpmaven-env/bin/activate
pip install serpmaven
```

In a new terminal, run `source serpmaven-env/bin/activate` again before using `serpmaven`.

The first run offers to download its browser (about 150 MB, once per machine);
`serpmaven install` does it ahead of time, and `serpmaven doctor` ends with `ready` when the
browser starts and looks like a desktop.

<details>
<summary><b>With uv: one command, no Python or virtual environment needed</b></summary>

<br>

```bash
uv tool install serpmaven
```

No uv yet? It installs with one line too: `curl -LsSf https://astral.sh/uv/install.sh | sh` on
macOS and Linux, `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
on Windows; then open a new terminal, so it finds the commands. To try SERP Maven without
installing it, put `uvx` in front of any command below: `uvx serpmaven search ...`.

</details>

<details>
<summary><b>On a Linux server (no screen)</b></summary>

<br>

The browser runs with a window, so a server needs a virtual display, and
software WebGL when there is no GPU:

```bash
sudo apt install xvfb
serpmaven install --with-deps
export SERPMAVEN_BROWSER_ARGS=--enable-unsafe-swiftshader
xvfb-run -a serpmaven doctor
```

Then start every command with `xvfb-run -a`. The [Docker image](#docker) has
all of this built in.

</details>

## Quickstart

> [!NOTE]
> You need a residential proxy with **sticky sessions**.

Save your proxy login once. For NodeMaven it asks for the Proxy Username and Proxy Password
from the dashboard:

```bash
serpmaven login
serpmaven search "best running shoes" --country us -o results.jsonl
```

Any other provider: save its proxy URL, with `{session}` where it expects a session id in the
username:

```bash
serpmaven login --proxy "http://USER-session-{session}:PASS@gate.example.com:7000"
serpmaven search "best running shoes" -o results.jsonl
```

The first query of a first run takes a few minutes while the first proxy IP warms
up on a few ordinary pages; the terminal shows each step. A run within a day of
one that renewed its cookie set (every 5 served queries by default) starts from
that set and skips it. Later queries take seconds, and
new proxy IPs start from its cookies. Every run ends with the pages served and
the traffic used, warm-up apart from the pages.

A file of queries, one per line: `serpmaven search -f queries.txt -o results.jsonl`.
`--proxy`, `--nodemaven`, `SERPMAVEN_PROXY` and `NODEMAVEN_LOGIN` / `NODEMAVEN_PASSWORD`
win over the saved login; `serpmaven logout` deletes it.
Through NodeMaven, each proxy IP's session id starts with `serpmaven`, so the
traffic shows up as the scraper's on your account; other proxies get the plain id.

> [!TIP]
> 🎁 **2 GB of free traffic.** No NodeMaven account yet? [Sign up](https://dashboard.nodemaven.com/accounts/signup/?utm_source=github&utm_medium=readme&utm_campaign=google_browser_scraper&utm_content=signup_promo) and enter the code **GOOGLENM**. Once per account.

<details>
<summary><b>Cap a run's traffic with a demo sub-user</b></summary>

<br>

`--nodemaven-demo` creates a sub-user with a traffic limit from your dashboard API
key, shares it across workers, and deletes it when the run ends (`--keep-subuser`
keeps it). The account needs sub-users enabled. A fresh warm-up alone uses about
60 MB, so keep the cap well above that:

```bash
export NODEMAVEN_API_KEY=...
serpmaven search -f queries.txt --nodemaven-demo --traffic-limit-mb 300 -o results.jsonl
```

</details>

<details>
<summary><b>Try it without a proxy</b></summary>

<br>

```bash
serpmaven search "best running shoes" --no-proxy --output data.json
```

Google then sees one address for every query, so it starts answering with
captchas sooner than it would across a pool of proxy IPs. Those pages come back as
`captcha` in `page_verdict`, not as empty results.

</details>

### From Python

```python
from serpmaven import ProxyTemplate, Scraper, Settings

proxy = ProxyTemplate("http://USER-session-{session}:PASS@gate.example.com:7000")
for page in Scraper(proxy, Settings(pages=2)).run(["best running shoes"]):
    for result in page.get("organic_results", []):
        print(result["position"], result["title"], result["link"])
```

## What you get

SERP Maven <picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/nodemaven/serpmaven/main/assets/raven-dark.svg"><img src="https://raw.githubusercontent.com/nodemaven/serpmaven/main/assets/raven.svg" alt="" height="20" align="absmiddle"></picture> returns one JSON object per results page:

```json
{
  "search_metadata": {"status": "Success", "page_verdict": "ok", "fetched_with": "http"},
  "search_parameters": {"engine": "google", "q": "best running shoes", "page": 1},
  "organic_results": [
    {"position": 1, "title": "...", "link": "https://...", "snippet": "..."}
  ],
  "related_questions": [{"question": "..."}],
  "ai_overview": {"text": "..."},
  "exit": {"session": "...", "queries_on_exit": 1, "bytes": "..."}
}
```

The field names are the ones common to SERP APIs, so simple consumers can often
switch with minimal mapping.

<details>
<summary><b>All fields</b></summary>

<br>

- `organic_results`, `ads`, `related_questions`, `related_searches`, `pagination`, `search_metadata`
- `ai_overview`: the text of Google's AI Overview when the page shows one (pages in English, `--hl en`, the default)
- `local_results`: the places of the map pack on local queries (name, rating, reviews, type, address, phone, opening hours)
- `exit`: which proxy IP (sticky session) answered and how many bytes the page cost
- `search_metadata.fetched_with`: `http` or `browser`
- `search_metadata.timings`: seconds per step (`http` for a page fetched over HTTP; `type`, `load`, `ready` and `read` for one the browser rendered; `parse`; `links`, which overlaps the next query)
- A query that failed is a record too, with an `error`
- `search_metadata.cached`: `true` on a record answered from `--cache-ttl`

Knowledge panels, shopping, images and other special blocks are not parsed.
`--format csv` writes the organic results as a table, and `--price-per-gb` adds
the cost per 1000 pages to the summary. Before a run starts, `search` prints a
rough estimate of its proxy traffic and time (and cost, with `--price-per-gb`)
for the queries it still has to ask; `-v` leaves it out.

</details>

## Poisoned results protection

Google does not always block a scraper. Sometimes it answers with a normal-looking
results page for a different query: no captcha, no error, just wrong data that
slips into your pipeline unnoticed.

The scraper checks results pages against the queries it asked. When a proxy IP
starts returning unrelated results, it is dropped within a few pages and the query
is asked again on a fresh one.

- No extra requests, no extra traffic: the check runs on the page already downloaded
- On by default

## Run it as an API

```bash
serpmaven serve --country us --prewarm
curl "http://127.0.0.1:8000/search.json?q=best+running+shoes"
```

In PowerShell, call `curl.exe` (plain `curl` there is another command).

- Same record format as above
- `--host` and `--port` set the address (default `127.0.0.1:8000`)
- `pages=N` returns N result pages for one query, up to `--max-pages` (default 3); more is answered 400. One page is a single record, more are a JSON array of records
- `hl` (language) and `gl` (country) per request: `/search.json?q=...&hl=de&gl=at`
- `--prewarm` finds a proxy IP Google serves at startup, so the first request does not wait for a warm-up; `/health` answers 503 `warming` until then
- Results pages are read one at a time on one held proxy IP; a page's links resolve in the background, so the next request starts while the last one waits for its links
- `links=N` resolves the links of the first N organic results only (`0`: none)
- `links_wait=S` answers after S seconds with the links resolved by then; links still being resolved are `null` and counted in `search_metadata.links_pending`
- `async=true` answers at once with `search_metadata.id`; `GET /searches/<id>.json` answers `"status": "Processing"` until the search and its links are done, then the record. The newest 500 searches are kept there, each for up to an hour; up to 50 async searches wait for the browser at once, more are answered 429
- Set `SERPMAVEN_API_KEY` to require a key; without one the server only listens on localhost

## Use it from an AI agent (MCP)

```json
{
  "mcpServers": {
    "google-search": {
      "command": "serpmaven",
      "args": ["mcp", "--country", "us", "--prewarm"]
    }
  }
}
```

It uses the login saved with `serpmaven login`; or add
`"env": {"NODEMAVEN_LOGIN": "...", "NODEMAVEN_PASSWORD": "..."}`. One tool,
`google_search(query, pages, hl, gl, links)`. It asks Google's Web results
only (`--lean`, on by default for `mcp`) and returns the organic results; start
it with `--no-lean` to also get "People also ask", related searches, and the AI
Overview and map places when Google shows them. `links` is how many of the top results get their full URL
(default 5, or `--links N` on the `mcp` command); fewer answers sooner, and a
result without one carries its site when Google shows it.
Positions run on across the pages of one answer. A call repeated with the same
arguments within 10 minutes gets the first answer back, with no new search.
When it cannot search at all - no saved login, no browser, or a flag the proxy
does not take - the server still starts and answers every call with what to fix.

## Docker

A ready image is published to GitHub Container Registry with every release:

```bash
docker run --rm ghcr.io/nodemaven/serpmaven doctor
docker run --rm -p 8000:8000 -e SERPMAVEN_API_KEY=change-me \
  -e SERPMAVEN_PROXY='http://USER-session-{session}:PASS@gate.example.com:7000' \
  ghcr.io/nodemaven/serpmaven serve --host 0.0.0.0
```

To build it yourself: `docker build -t serpmaven .`

## How it works

1. **New identity:** a sticky proxy session and a fresh browser profile.
2. **Warm-up:** with no cookie set to start from (kept by an earlier run or given
   with `--cookies`), the first proxy IP opens a few ordinary pages, staying on
   each for 20-45 seconds. Later ones start from the Google cookies of a served
   proxy IP and are warmed up only when no such set is accepted.
3. **Search:** the first query on a proxy IP goes through the browser. Once Google
   serves it, the next pages come over HTTPS with the browser's cookies, until
   Google asks for its check again.
4. **Check before parsing:** a captcha or a block is reported as one, never
   parsed as an empty result.
5. **Keep or drop:** a proxy IP Google answers is reused; one it refuses is dropped
   and the query retried on a new one. If one proxy IP after another is refused, the run
   stops instead of spending more traffic.

<details>
<summary><b>More detail: HTTP between checks, holding proxy IPs, copied cookies, result links, traffic</b></summary>

<br>

**HTTP between checks.** After the browser passes Google's check, results pages
are fetched over HTTPS with the browser's own headers and cookies. If that stops
working, the scraper goes back to the browser on its own. `--no-hybrid` uses the
browser for every page.

**Unrelated results.** A proxy IP that starts returning results unrelated to the
queries is dropped, and the query is asked again on a new one.

**Holding proxy IPs.** A served proxy IP is kept for up to 300 queries. Every new one
pays for a browser check, and for a warm-up when no cookie set is accepted, so a
short cap mostly buys more of that traffic. Every 50
results pages it renders, the browser is restarted on the same profile and proxy IP, which keeps
its memory from growing over a long hold. A browser error on a search is retried
once on the same proxy IP before it is dropped; a network error, running out of
memory or a crashed browser drops it at once.

**Starting from saved cookies.** `--cookies FILE` gives every new proxy IP the
Google cookies of a browser Google already serves, so it skips the warm-up; the
file can come from another machine. `--renew-cookies N` tries every N served
queries on a proxy IP to replace that set with that proxy IP's cookies (skipped
when they hold no `NID`), so it stays fresh, and without `--cookies` the first
renewal that yields Google's cookies ends the warm-ups. It is on by default,
every 5 queries, so a run warms up at most its first proxy IP and rarely another.
`--renew-cookies 0` turns renewal off and keeps no sets: a set given with
`--cookies` stays in use until two proxy IPs in a row are refused on their first
query with it, and without sets every new proxy IP warms up (unless `--warmup
off`). Renewed sets are kept in the config folder, readable by their owner
only, and the next run starts from those up to 24 hours old, so it skips the
warm-up too, if it uses the same browser build (engine, channel and version):
a set made by another build is refused more often, so after an update a run
starts fresh. `--cookies-out DIR` keeps them in DIR instead. `--no-keep-cookies`
neither reads nor keeps them in the config folder; a `--cookies-out DIR` given
with it is still written, since it was asked for. If every set is refused in turn, new proxy IPs are warmed up again,
and with `--renew-cookies` the next successful renewal brings the sets back. A file is a
JSON list of Playwright cookie records, or an object with that list under
`"cookies"`, and must hold Google's `NID` cookie.

**Result links.** Google wraps result links in encrypted `/goto` redirects.
Each one is resolved with a small request on the proxy IP the page came from,
with that page's cookies, in the background while the next query runs; a link
left over is tried through a separate short-lived proxy session. Google answers
only a few links of a page at once and holds the rest on a "Verifying your
request" page; a link held for up to 60 seconds is asked again when the wait is
over, twice at most and within 75 seconds after the page's first lookups (up to
30 seconds), so a page's links can take a minute or two while the queries carry on.
Records still come out in the order each browser searched them.

**Traffic.** Everything goes through a small local relay that counts bytes per
host and refuses what no search needs: the models and data files Chrome
otherwise downloads into every fresh profile, and the videos of a warm-up page.

</details>

The defaults come from measurements in
[proxy-benchmark](https://github.com/nodemaven/proxy-benchmark).

## Options

<details>
<summary><b>All search options</b></summary>

<br>

| Option | Default | What it does |
|---|---|---|
| `-f`, `--file FILE` | | Queries from FILE, one per line |
| `-o`, `--output FILE` | stdout | Where the results are written |
| `--resume` | | Skip the queries the `--output` file already has results for and add to it (jsonl); failed ones are asked again |
| `--workers N` | 1 | Search on N proxy IPs at once, one browser each, from one list of queries |
| `--max-per-exit N` | 300 | Queries before a served proxy IP is retired |
| `--restart-every N` | 50 | Results pages the browser rendered before it restarts on the same profile and proxy IP; `0` never |
| `--no-hybrid` | | Fetch every results page in the browser instead of over HTTP between Google's checks |
| `--cut-http-pages` | | Experimental: stop reading a results page that comes over HTTP at Google's footer, so the scripts after it are not downloaded |
| `--hl`, `--gl` | `en`, unset | Language and country of the results |
| `--location PLACE` | unset | Search as from this place, by its canonical name: `"Austin,Texas,United States"` |
| `--tbs FILTER` | unset | Google's `tbs` filter as it is, e.g. `qdr:w` for the past week |
| `--no-personalization` | | Results without personalization (`pws=0`) |
| `--no-autocorrect` | | Search a misspelt query as typed (`nfpr=1`) |
| `--cr COUNTRIES`, `--lr LANGUAGES` | unset | Only documents from these countries (`countryUS`) or in these languages (`lang_en`) |
| `--lean`, `--no-lean` | off; `mcp` on | Google's Web results only (`udm=14`): the organic links without the AI Overview, "People also ask", related searches or map places; pages about a third the size |
| `--cookies FILE` | | Start every new proxy IP from the Google cookies in FILE instead of a warm-up; repeat for more files, the next one taking over after two proxy IPs in a row are refused on their first query, and warm-ups again once every file has been refused that way |
| `--renew-cookies N` | 5 | Every N served queries, try to make a proxy IP's Google cookies the set new proxy IPs start from (skipped when they hold no `NID`); `0` turns renewal off: without `--cookies`, every new proxy IP warms up (unless `--warmup off`) |
| `--cookies-out DIR` | | Keep renewed cookie sets in DIR instead of the config folder; the next run starts from them, or pass them to `--cookies` |
| `--no-keep-cookies` | | Do not start from cookie sets an earlier run kept, and do not keep this run's in the config folder; a `--cookies-out DIR` given with it is still written |
| `--warmup` | `L3` | Pages a fresh proxy IP opens before its first query: `L3`, `light` (without Google Trends: much less traffic, served on the first query less often), `L2`, `L1`, `off` |
| `--spare N` | 0 | Proxy IPs warmed in the background so a retired one is replaced at once; each costs a warm-up |
| `--gap LOW,HIGH` | 2,5 | Seconds between queries on one proxy IP |
| `--pages N` | 1 | Result pages per query |
| `--format` | from `-o`, else `jsonl` | `jsonl`, `json` or `csv`; `-o results.csv` picks `csv` by itself |
| `--patient MINUTES` | `0` (stop) | When Google refuses proxy IPs until the run stops, wait and go on with the queries left, up to 3 times; a query cut short is asked again from its first page, so its earlier pages appear twice in the output |
| `--price-per-gb` | | Your proxy price, to print the cost per 1000 pages |
| `--cache-ttl MINUTES` | `0` (off) | Answer a query from an earlier run's results if they are at most this old, without the browser or the proxy |
| `--cache-dir DIR` | | Where `--cache-ttl` keeps results (default: a `cache` folder beside the saved login) |
| `--no-resolve-links` | | Keep result links as Google `/goto` redirects |
| `--links N` | all; `mcp` 5 | Resolve the links of the first N organic results on each page only; the rest keep their `/goto` redirect. Fewer answers sooner |
| `--save-html DIR` | | Keep every page's HTML, gzipped |
| `--channel NAME` | | Browser channel, e.g. `chrome` for the installed Chrome |
| `--timezone ZONE` | | IANA timezone for the browser, e.g. `Europe/Berlin` |
| `--browser-arg=ARG` | | Extra Chromium switch, e.g. `--browser-arg=--disable-gpu`; repeat for more, or set `SERPMAVEN_BROWSER_ARGS` |
| `--headless` | | Run the browser without a window; `serpmaven doctor --headless` shows what a website sees then |
| `--no-relay` | | Hand the proxy to the browser directly: traffic is not counted, and every results page is fetched in the browser |
| `-v`, `--verbose` | | Print every step as a JSON line on stderr instead of the progress line, after one plain `proxy: ...` line; the run ends with `summary: {...}` |

</details>

## Proxies

Any provider with sticky sessions works through `--proxy` and a `{session}`
placeholder. A proxy without one is accepted with a warning: if the proxy IP changes
on every request, warming it does nothing. SOCKS5 needs an IP-whitelisted
endpoint, because Chrome cannot send a SOCKS5 username and password.

## Check your machine

```bash
serpmaven doctor
```

Starts the browser on a blank page, sends nothing, and reports what a website
would see: whether the browser looks headless, whether WebGL works, the screen
size. Google answers some machines much less than others, so run this first.

## What this is not

Not a free Google Search API, and not a guarantee that every proxy IP will
work. It is a browser-based collector that makes failures explicit, drops proxy IPs
Google refuses, and tells you what each page cost.

## Status

Early. The `cloak` engine (`--engine cloak`) is experimental; `patchright` is
the default.

Google changes its pages often. If results come back empty or wrong, run with
`--save-html DIR` and open an issue with what you see.

You are responsible for using this within Google's terms and the law where you
run it.

## Adding an engine

An engine joins this tool only after it has been measured on
[proxy-benchmark](https://github.com/nodemaven/proxy-benchmark). Add it there
and run the warm-up ladder against Google:

```bash
python scripts/run_ladder.py --engines your-engine
```

Then open a pull request here with the run file. The ladder runs identities
with and without the warm-up in the same hours, so the result shows whether the
engine actually gets served, not whether it got lucky once.

## License

[MIT](https://github.com/nodemaven/serpmaven/blob/main/LICENSE)

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/nodemaven/serpmaven/main/assets/raven-animated-dark.svg"><img src="https://raw.githubusercontent.com/nodemaven/serpmaven/main/assets/raven-animated.svg" alt="SERP Maven raven" height="64"></picture><br><sub>SERP Maven by <a href="https://nodemaven.com/?utm_source=github&utm_medium=readme&utm_campaign=google_browser_scraper&utm_content=footer">NodeMaven</a></sub></p>
