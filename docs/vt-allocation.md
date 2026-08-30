# VT allocation (`vt_allocation.py`)

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

Source chain, tried in freshness order: monthly JSON endpoint → quarterly fact-sheet
PDF (separate host, outside the interactive site's bot protection) → last saved
value → manual entry. **It never guesses silently.** `FALLBACK_VT_US_PCT` is only
ever a *suggested default* in the manual prompt.

The word on screen is **saved**, never "cached": the value came out of the user's own
config file, which is the same file the accounts and the band come from, and one word
for it means one thing to learn. `source="cache"` stays as the internal identifier,
the same split `Trade` versus "order" keeps.

**The fund is spelled out the first time a run names it and abbreviated after** --
"Looking up Vanguard Total World Stock ETF's (VT) current U.S. and international stock
allocation...", then "VT's" everywhere below. `VT_FUND_NAME` and `VT_TICKER` are the
constants; the *rule* lives in `resolve_vt_allocation`'s `vt_possessive`, a two-line
closure, because which line speaks first depends on which source answers: `--offline`
never prints the lookup line at all, and the manual prompt is then the first thing to
name the fund. Baking the long form into one message would leave the other paths
saying "VT" with nothing having said what it stands for.
`test_the_fund_is_spelled_out_once_however_the_run_reaches_it` holds it.

**Both halves of the split are named, in prose.** "Found 62% U.S. stocks and 38%
international stocks (as of July 31, 2026)." -- not "62% U.S. / 38% international",
and not the U.S. share alone with the remainder left as an exercise. The confirmation
below it is "Use these values?", plural, because two values are on screen; see the
confirmation rule under "Output structure".

The fund page's HTML is deliberately not scraped -- client-side rendered behind bot
protection. Don't "improve" this by adding a scraper.

**A dead source in this chain is silent, and one was.** The JSON endpoint's URL sat
under `/investment-products/etfs/profile/`, where the SPA router answers *every* path --
real or invented -- with `200` and the HTML app shell. So the primary source failed on
every run for every user, the chain quietly served the quarterly PDF, and the two claims
that make the chain worth having ("monthly", "two independent sources") were both false
while the tests stayed green. The URL is now `vmf/api/{ticker}/diversification`, which
is what the page's own bundle calls. Two things follow. **A URL here is verified against
a live response body, never a status code** -- `curl -sI` cannot tell an endpoint from
the router's catch-all. And **the whole suite mocks the network by design**, so nothing
in CI can ever notice this. `tests/test_network_sources.py` is the cover for that blind
spot -- `pytest -m network`, deselected by default -- and it is a manual act, run before
a release or when the chain misbehaves. Five of its seven tests fail against the dead
URL, including the one that asserts the chain reaches the *primary* source rather than
merely returning something.

**Order `requests.exceptions.JSONDecodeError` before `requests.RequestException`.** It
subclasses *both* that and `ValueError`, so a decode clause placed after the network
clause never fires, and a 200 carrying HTML gets reported as "Failed to download" -- a
parse failure described as an outage, which is most of why the above went unnoticed for
as long as it did. `test_a_non_json_body_is_not_reported_as_a_download_failure` pins it,
and the fake response in `tests/test_vt_allocation.py` raises requests' real subclass
rather than a bare `ValueError` -- with a bare one, the mis-ordered version passes.

**`_extract_us_pct_from_diversification` is total: only `VTFetchError` leaves it.**
`fetch_vt_us_pct` and `resolve_vt_allocation` catch that and nothing else, and `cli.run`
has no broad handler, so anything else escaping crashes the run over an upstream
response the user cannot influence -- the same standard `persistence.config_from_dict`
holds itself to, and it is structured the same way: named errors from
`_parse_diversification`, then a catch-all that re-raises `VTFetchError` untouched. Three
shapes used to escape -- a non-string `name`, a non-list `item`, and a NaN percentage.
The NaN is the one to remember: `json.loads` accepts a bare `NaN` literal, `Decimal` then
builds a NaN happily, and comparing it *signals* `InvalidOperation` instead of returning
`False`, so the range check itself was the thing that raised. Hence the `is_finite()`
test in front of it.

**The two URLs are for two different readers, and are not interchangeable.**
`VT_FACT_SHEET_URL` is the PDF the *fetch chain* falls back to: a static file on a
separate host, which is what makes it a good second source and a poor thing to hand a
person. `VT_FUND_PAGE_URL` is what the manual-entry prompt names, because someone
reading the number off for themselves wants the page they would reach from a search or
from their broker -- current rather than quarterly, and carrying the same country table
the JSON endpoint backs. Being unscrapable is irrelevant to a human.
