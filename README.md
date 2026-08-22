# three-fund-rebalance

An interactive CLI that computes the trades needed to rebalance a
[three-fund portfolio](https://www.bogleheads.org/wiki/Three-fund_portfolio)
(U.S. stocks / international stocks / bonds) to a target stock and bond
allocation, across any number of investment accounts -- tax-deferred
(traditional IRA, 401(k), ...), tax-free (Roth, HSA) and taxable alike.

It asks for your target stock and bond allocation, derives the U.S. versus
international stock mix from
[VT](https://investor.vanguard.com/investment-products/etfs/profile/vt)'s
current market-cap weighting, asks how far you'll let an asset class drift
before correcting it, asks about each of your accounts and what they hold, and
then prints the trades needed to reach your target -- buys, sells, exchanges --
keeping bonds out of taxable accounts, keeping the rebalancing trades
themselves inside tax-advantaged ones, and preferring tax-deferred space over
Roth space for the bonds, to minimize tax drag.

## Disclaimer

Not affiliated with, endorsed by, or sponsored by Vanguard, Fidelity, or any
broker or fund company. Fund and product names are used only to identify what
you hold.

**This is not financial, investment, or tax advice.** It is a calculator that
arithmetic-checks a portfolio against a target you choose. Review every order
it suggests before placing it, and consult a qualified professional if you
want advice.

Some limits worth knowing about specifically:

- It collects no cost-basis data, so it cannot compute capital gains. The
  rebalance minimizes taxable trade *volume* as a proxy for tax cost -- a
  heuristic, not a gains calculation. Selling in a taxable account may realize
  a tax liability this tool never sees.
- The wash-sale check is a warning, not a guarantee. It matches funds by the
  name you type, so two share classes of the same index (VTI and VTSAX) are
  not recognized as the same security even though the IRS may treat them as
  substantially identical. And because it cannot see your cost basis, it
  cannot tell whether a sale is at a loss -- which is the only case where the
  rule bites. Check your lots before placing the orders.
- Preferring international funds in taxable accounts is a rule of thumb, not
  a calculation. The credit is worth a couple of basis points a year and is
  partly offset by international funds' higher, less-qualified dividends; the
  tool weighs neither, and does not check that a given fund is majority-foreign
  and therefore actually eligible to pass the credit through.
- It knows nothing about contribution or withdrawal limits, holding periods,
  early-withdrawal penalties, or restrictions on what a given account can
  actually hold -- a 401(k)'s fixed fund menu, for instance.
- VT's U.S./international allocation is fetched from Vanguard and may be
  stale, or may fail and fall back to a cached or manually entered value.

## Install

Requires Python 3.10+. Install with [pipx](https://pipx.pypa.io/), which puts
the CLI on your PATH in its own isolated environment:

```bash
pipx install git+https://github.com/melvinrajendran/three-fund-rebalance
```

If you don't have pipx yet: `brew install pipx && pipx ensurepath` on macOS,
or `python3 -m pip install --user pipx && python3 -m pipx ensurepath`
elsewhere.

[uv](https://docs.astral.sh/uv/) does the same job and will fetch a suitable
Python for you if your system one is too old:

```bash
uv tool install git+https://github.com/melvinrajendran/three-fund-rebalance
```

Either way, `three-fund-rebalance` then works from any directory. To remove
it: `pipx uninstall three-fund-rebalance` (or `uv tool uninstall`).

## Updating

```bash
pipx install --force git+https://github.com/melvinrajendran/three-fund-rebalance
```

The `--force` is deliberate. The version string in `pyproject.toml` doesn't
change on every commit, so `pipx upgrade` compares versions, sees the
installed one as current, and skips the refetch; `--force` reinstalls from
the latest commit unconditionally. The uv equivalent is
`uv tool install --force git+https://...`.

Your saved portfolio config lives outside the installation at
`~/.three_fund_rebalance/config.json`, so updating -- and even uninstalling
-- never touches it. A config written by an older version is upgraded in
place the first time a newer version saves over it.

## Running

```bash
three-fund-rebalance
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--config PATH` | Portfolio config file to read/write (default `~/.three_fund_rebalance/config.json`) |
| `--fresh` | Ignore any existing config file and start blank |
| `--no-save` | Don't offer to persist this run's answers |
| `--offline` | Skip the live VT fetch; use the cached or a manually entered value |
| `--vt-us-pct PCT` | Manually set VT's U.S. % and skip looking it up or prompting for it |
| `--version` | Print the installed version and exit |

Each run walks three numbered steps -- your target asset allocation, when to
rebalance, and your account holdings -- and then prints a rebalancing summary.
The third step asks for each account's type, a unique nickname, whether it
holds individual funds or a single target-date fund, which of those funds it
holds, and any cash available to invest. If you've run it before, your saved
accounts are offered back with their last-known values pre-filled as editable
defaults -- press Enter to keep a value or type a new one.

## How it works

- **VT's U.S./international allocation** comes from two independent Vanguard
  sources, tried in freshness order. First the JSON endpoint behind the fund
  profile page's country diversification table, refreshed **monthly**; if
  that fails, Vanguard's **quarterly** fact sheet PDF
  (`https://fund-docs.vanguard.com/F3141.pdf`), a static file on a separate
  host outside the interactive site's bot protection. If both fail, the CLI
  falls back to your last cached value, and finally to manual entry -- it
  never guesses silently. The fund page's own HTML is deliberately not
  scraped: it's client-side rendered behind bot protection, so it would need
  a headless browser and would still break often.
- **Each account holds one kind of thing**: either a single target-date fund
  or some combination of a U.S. stock fund, an international stock fund and a
  U.S. bond fund. Cash can sit alongside either. Different accounts in the
  same portfolio can be of different kinds.
- **Target-date funds** are entered as a single position with their own
  U.S. stock / international stock / bond mix (from the fund's fact sheet),
  and are folded into the overall allocation math as a fixed-ratio bundle.
  Because such an account holds nothing else, its value is fixed: the tool
  never sells a target-date fund, only invests that account's cash into it. Its sleeves still count toward your overall
  allocation, so the rest of the portfolio works around them.
- **Cash available to invest** in an account counts toward that account's
  total and is always fully invested by the plan.
- **A rebalancing band** decides how far an asset class may drift from its
  target before it's worth correcting, in percentage points of the whole
  portfolio. Rebalancing to an exact target means every drift generates
  trades, however small, and in a taxable account those cost real money to
  correct a rounding error. The default is 5 points, the long-standing
  Bogleheads convention; enter 0 to rebalance to the exact target. Inside the
  band your allocation is left alone -- but cash is still invested, and free
  trades in tax-advantaged accounts are still made. Outside it, the class
  moves back to the nearest edge.
- **What to hold is decided before where to hold it.** The band settles what
  each asset class should be worth -- staying put when it's close enough,
  and directing any available cash at whichever class is furthest below
  target. Only then does the solver work out which accounts should hold it.
  The phases below can move a holding between accounts, which never changes
  an asset class total; they can't decide you should own less international
  because it's inconveniently placed.
- **Rebalancing** is computed as a small linear program, solved in six
  lexicographic phases: (1) minimize bonds left in taxable accounts --
  bonds fill sheltered capacity first and only spill into taxable once
  that's exhausted; (2) minimize $ trade volume within taxable accounts, as
  a proxy for avoiding capital gains (no cost-basis data is collected, so
  this is an approximation, not an exact gains calculation); (3) avoid
  selling a fund in a taxable account while buying the same fund in a
  tax-advantaged one, which risks a wash sale; (4) hold bonds in tax-deferred
  accounts rather than tax-free ones, since a Roth or HSA never taxes the
  growth it shelters and is better spent on stocks; (5) prefer to
  hold the international fund in taxable accounts, where the foreign tax
  withheld on it can be claimed as a credit that a tax-advantaged account
  forfeits; (6) tie-break by minimizing total trade volume everywhere.
  Everything below (2) is free rearrangement only: those phases decide which
  funds an account already being traded ends up holding, and never open a
  taxable trade of their own. Each account's
  total value is fixed -- a rebalance only reallocates *within* an account,
  never moves money between accounts. Orders under $1 (Fidelity's fractional-share
  minimum, the smaller of its Roth IRA and taxable brokerage minimums) are
  dropped as impractical.

## Development

Clone the repo and install it in editable mode in a virtualenv:

```bash
git clone https://github.com/melvinrajendran/three-fund-rebalance
cd three-fund-rebalance
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

With the venv active, run the CLI straight from your working copy:

```bash
python -m three_fund_rebalance.cli
```

Run it as a module rather than through the `three-fund-rebalance` console
script. That always executes the working copy you are editing, whether or not
a pipx or uv install of the same name is also on your PATH -- so there is
never a question about which one you just tested.

Editable mode means edited modules -- **and newly added ones** -- take effect
on the next run with no reinstall. Rerun `pip install -e ".[dev]"` only when
the packaging metadata changes rather than the code: a new or bumped
**dependency**, a renamed **console script** under `[project.scripts]`, or a
new **top-level package** (new modules *inside* `three_fund_rebalance/` do
not count). The command is cheap and idempotent, so when in doubt just run it.

Rebuild the venv from scratch if the required Python version rises, or if the
environment gets into an inconsistent state (for example `pyvenv.cfg`
reporting a different version than `.venv/bin/python` actually is):

```bash
rm -rf .venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Tests and lint:

```bash
pytest                              # run the test suite
pytest --cov=three_fund_rebalance --cov-report=term-missing  # with coverage
ruff check three_fund_rebalance tests   # lint
```

## License

[MIT](LICENSE).
