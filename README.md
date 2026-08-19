# three-fund-rebalance

An interactive CLI that computes the trades needed to rebalance a
[three-fund portfolio](https://www.bogleheads.org/wiki/Three-fund_portfolio)
(domestic equity / international equity / bonds) to a target stock/bond
allocation, across any number of investment accounts -- tax-advantaged
(Roth/Traditional IRA, 401(k), HSA, ...) and taxable alike.

It asks for your target stock/bond split, derives the domestic/international
equity split from
[VT](https://investor.vanguard.com/investment-products/etfs/profile/vt)'s
current US/ex-US market weighting, asks about each of your accounts and what
they hold, and then prints the buy/sell/exchange trades needed to reach your
target -- favoring tax-advantaged accounts for both bonds and rebalancing
trades, to minimize tax drag.

## Setup

Requires Python 3.10+. Note that `python3` on macOS may still be the system
3.9 -- use an explicit interpreter (e.g. `python3.12`) if so.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Putting it on your PATH

The generated console script hard-codes an absolute path to the venv's
Python in its shebang, so it runs correctly from anywhere without the venv
being activated. Symlink it into a directory already on your PATH:

```bash
ln -sf "$PWD/.venv/bin/three-fund-rebalance" ~/.local/bin/three-fund-rebalance
```

Then `three-fund-rebalance` works from any directory. Because the package is
installed in editable mode (`-e`), code changes take effect immediately with
no reinstall. If you ever delete and rebuild `.venv`, the symlink keeps
working as long as the rebuild recreates the same path.

If you'd rather keep it isolated from this repo's venv,
[pipx](https://pipx.pypa.io/) does the same job and manages the PATH entry
for you:

```bash
brew install pipx && pipx ensurepath
pipx install -e /path/to/three-fund-rebalance
```

## Updating

```bash
git pull
```

That is usually the whole update. The package is installed in editable mode
(`-e`), so the venv resolves `three_fund_rebalance` straight to this source
directory -- edited modules **and newly added ones** take effect on the next
run with no reinstall. The PATH symlink keeps working too, since it points at
the venv's console script rather than at any particular version.

Reinstall when the packaging metadata changes rather than the code:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

Specifically, that is needed after a change to `pyproject.toml` that
adds or bumps a **dependency**, renames the **console script** under
`[project.scripts]`, or introduces a **new top-level package** (new modules
*inside* `three_fund_rebalance/` do not count). The command is cheap and
idempotent, so when in doubt just run it.

Rebuild the venv from scratch if the required Python version rises, or if the
environment gets into an inconsistent state (for example `pyvenv.cfg`
reporting a different version than `.venv/bin/python` actually is):

```bash
rm -rf .venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The symlink from `~/.local/bin` survives this, because the rebuild recreates
the same path. After any update, a quick check that things still work:

```bash
pytest
three-fund-rebalance --help
```

Your saved portfolio config lives outside the repo at
`~/.three_fund_rebalance/config.json`, so pulling, reinstalling, and even
deleting `.venv` never touch it.

## Running

```bash
three-fund-rebalance
```

or, without the console script (requires the venv to be active):

```bash
python -m three_fund_rebalance.cli
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--config PATH` | Portfolio config file to read/write (default `~/.three_fund_rebalance/config.json`) |
| `--fresh` | Ignore any existing config file and start blank |
| `--no-save` | Don't offer to persist this run's answers |
| `--offline` | Skip the live VT fetch; use the cached or a manually entered value |
| `--vt-us-pct PCT` | Manually set VT's US % and skip fetching/prompting for it entirely |

On each run you're asked for your target stock/bond split, then your
accounts (type, a unique nickname, and which of a domestic equity fund,
international equity fund, domestic bond fund, and/or target-date fund each
one holds, plus any uninvested cash). If you've run it before, your saved
accounts are offered back with their last-known balances pre-filled as
editable defaults -- press Enter to keep a value or type a new one.

## How it works

- **VT's US/ex-US weighting** comes from two independent Vanguard sources,
  tried in freshness order. First the JSON endpoint behind the fund profile
  page's country diversification table, which is refreshed **monthly**; if
  that fails, Vanguard's **quarterly** fact sheet PDF
  (`https://fund-docs.vanguard.com/F3141.pdf`), a static file on a separate
  host outside the interactive site's bot protection. If both fail, the CLI
  falls back to your last cached value, and finally to manual entry -- it
  never guesses silently. The fund page's own HTML is deliberately not
  scraped: it's client-side rendered behind bot protection, so it would need
  a headless browser and would still break often.
- **Target-date funds** are entered as a single position with their own
  domestic/international/bond split (from the fund's fact sheet), and are
  folded into the overall allocation math as a fixed-ratio bundle -- a TDF
  can coexist with individual funds in the same account.
- **Uninvested cash** in an account counts toward that account's investable
  total and is always recommended to be fully invested.
- **Rebalancing** is computed as a small linear program, solved in three
  lexicographic phases: (1) minimize bonds left in taxable accounts --
  bonds fill tax-advantaged capacity first and only spill into taxable once
  that's exhausted; (2) minimize $ trade volume within taxable accounts, as
  a proxy for avoiding capital gains (no cost-basis data is collected, so
  this is an approximation, not an exact gains calculation); (3) tie-break
  by minimizing total trade volume everywhere. Each account's total value is
  fixed -- a rebalance only reallocates *within* an account, never moves
  money between accounts. Trades under $1 (Fidelity's fractional-share
  minimum, the smaller of its Roth IRA and taxable brokerage minimums) are
  dropped as impractical.

## Development

```bash
pytest                              # run the test suite
pytest --cov=three_fund_rebalance --cov-report=term-missing  # with coverage
ruff check three_fund_rebalance tests   # lint
```
