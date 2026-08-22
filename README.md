# three-fund-rebalance

An interactive CLI that works out the trades needed to rebalance a
[three-fund portfolio](https://www.bogleheads.org/wiki/Three-fund_portfolio)
(U.S. stocks / international stocks / bonds) across any number of accounts --
tax-deferred, tax-free and taxable alike -- putting each asset class where it
is taxed least.

## Disclaimer

**Not investment, tax, or legal advice, and not a recommendation to buy or
sell.** That is what the tool prints at the foot of every report, and it means
what it says: this is a calculator that arithmetic-checks a portfolio against a
target you choose. Using it creates no advisory or fiduciary relationship, and
it places no orders -- it is not connected to your broker. Review each order
before placing it, and consult a qualified professional if you want advice. See
[Limitations](#limitations) for what it cannot see.

Not affiliated with, endorsed by, or sponsored by Vanguard, Fidelity, or any
broker or fund company. Fund and product names identify only what you hold.

## Example

Three accounts, an 80/20 stock and bond target, and a 5/25 rebalancing band:

```
Target asset allocation
-----------------------
  U.S. stocks            49.6%
  International stocks   30.4%
  Bonds                  20.0%

  From 80.0% stocks / 20.0% bonds, where stocks are split on VT's 62.0% U.S.
  allocation (June 30, 2026).

Rebalancing band
----------------
Plus or minus 5.0 percentage points, or 25.0% of an asset class's own target,
whichever is tighter:

  U.S. stocks           44.6% to 54.6%
  International stocks  25.4% to 35.4%
  Bonds                 15.0% to 25.0%

No trades while every asset class is inside its band; once one falls outside,
all three go back to target.

Your accounts
-------------

  Fidelity Brokerage (Brokerage, taxable)
    VTI (U.S. stock fund)            $60,000.00
    VXUS (international stock fund)  $30,000.00
    Total                            $90,000.00

  Fidelity Roth IRA (Roth IRA, tax-free)
    VTI (U.S. stock fund)  $20,000.00
    BND (bond fund)                --
    Total                  $20,000.00

  Employer 401(k) (Traditional 401(k), tax-deferred)
    VTI (U.S. stock fund)  $30,000.00
    BND (bond fund)        $10,000.00
    Total                  $40,000.00

"Tax-free" means qualified withdrawals only; Roth and HSA rules apply.

Current vs. target allocation
-----------------------------
Total portfolio value: $150,000.00
  Values as entered, not live market prices.

                                    Current              Target  Drift (pts)
  U.S. stocks           $110,000.00 (73.3%)  $74,400.00 (49.6%)        +23.7 *
  International stocks   $30,000.00 (20.0%)  $45,600.00 (30.4%)        -10.4 *
  Bonds                   $10,000.00 (6.7%)  $30,000.00 (20.0%)        -13.3 *

  * outside its rebalancing band

Orders to place
---------------
Review each order before placing it:

  Fidelity Brokerage (Brokerage)
    Exchange $15,600.00 from VTI to VXUS

  Employer 401(k) (Traditional 401(k))
    Exchange $20,000.00 from VTI to BND

If filled at the values you entered: 49.6% U.S. / 30.4% international / 20.0%
bonds

Selling $15,600.00 in your taxable accounts may realize capital gains or
losses; no cost basis is collected here, so that tax is not estimated.

Not investment, tax, or legal advice, and not a recommendation to buy or sell.
Consult a professional about your situation.
```

Note where the bonds went. The whole $30,000 sleeve lands in the 401(k)
rather than the Roth, because a Roth never taxes what it shelters and is
better spent on stocks -- and the Roth's own $20,000 of U.S. stock is left
untouched, because moving it would buy nothing. The taxable account sells only
what the target requires.

## Install

Requires Python 3.10+. Install with [pipx](https://pipx.pypa.io/), which puts
the CLI on your PATH in its own isolated environment:

```bash
pipx install git+https://github.com/melvinrajendran/three-fund-rebalance
```

If you don't have pipx: `brew install pipx && pipx ensurepath` on macOS, or
`python3 -m pip install --user pipx && python3 -m pipx ensurepath` elsewhere.
[uv](https://docs.astral.sh/uv/) does the same job and will fetch a suitable
Python if your system one is too old:

```bash
uv tool install git+https://github.com/melvinrajendran/three-fund-rebalance
```

To update, reinstall with `--force`. The version in `pyproject.toml` doesn't
change on every commit, so plain `pipx upgrade` sees the installed copy as
current and skips the refetch:

```bash
pipx install --force git+https://github.com/melvinrajendran/three-fund-rebalance
```

Your saved portfolio lives outside the installation at
`~/.three_fund_rebalance/config.json`, so updating -- or uninstalling -- never
touches it. Files written by older versions are upgraded in place on the next
save.

## Running

```bash
three-fund-rebalance
```

Three numbered steps -- your target asset allocation, when to rebalance, your
account holdings -- then the summary above. Run it again and your saved
accounts come back with their last values pre-filled: press Enter to keep one
or type a new one.

| Flag | Effect |
| --- | --- |
| `--config PATH` | Portfolio file to read and write (default `~/.three_fund_rebalance/config.json`) |
| `--fresh` | Ignore your saved portfolio and start blank |
| `--no-save` | Don't offer to save this run's answers |
| `--offline` | Skip the live VT fetch; use the cached or a manually entered value instead |
| `--vt-us-pct PCT` | Set VT's U.S. stock allocation % directly, skipping the lookup and the prompt |
| `--version` | Print the installed version and exit |

## How it works

**Where each asset class goes.** Bonds fill tax-advantaged space first,
tax-deferred before Roth. Rebalancing trades happen inside sheltered accounts,
where they cost nothing. International prefers taxable, where the foreign tax
withheld on it can be claimed as a credit that a sheltered account forfeits.
Only the first two of those will ever open a taxable trade -- the rest decide
which funds an account already being traded ends up holding.

**What to hold is decided before where to hold it.** The band settles what
each asset class should be worth -- staying put when nothing has drifted far
enough to matter, and directing available cash at whichever asset class is
furthest below target. Only then does the solver work out which accounts hold
it.

**The rebalancing band** is how far an asset class may drift before it is
worth correcting. Trading to an exact target means every drift generates
trades, and in a taxable account those cost real money to fix a rounding
error.

You set it with two numbers and an asset class has to satisfy both, so the
tighter one binds -- the 5/25 rule. The *absolute band* is in percentage
points of the whole portfolio; the *relative band* is a share of the asset
class's own target. Those are the names the prompts and the report use too.
Neither alone works for all three: 5 points below a 5% bond target is *zero
bonds*, while 25% of a 59% U.S. target is 14.7 points, far looser than
anything you'd want on the class that dominates the portfolio. The two cross
at a 20% target, where both come to 5 points. Zero on either means no band at
all, and every drift is corrected.

It is a trigger, not a destination. While every asset class sits inside its
band your allocation is left alone -- though free trades inside shelters are
still made. Once any asset class falls outside, all three go back to target
rather than to the band edge, which would leave you on the boundary and one
small drift from trading again.

Cash is spent before any of that is decided, and the band is then judged on
what it leaves behind. So a dividend swept into your settlement fund gets
invested at whichever asset class is furthest below target, and only
rebalances the rest of the portfolio if it was going to need rebalancing
anyway.

**Accounts hold one kind of thing**: either a single target-date fund or some
combination of a U.S. stock, international stock and U.S. bond fund. Cash sits
alongside either. A target-date fund is entered as one position with its own
internal mix, so its value is fixed -- the tool never sells one, it only
invests that account's cash into it, and works the rest of the portfolio
around its sleeves.

**Money never moves between accounts.** Each account's total is fixed; a
rebalance only reallocates within one, including investing its cash. Orders
under $1 are left out as impractical.

**VT's U.S./international allocation** comes from Vanguard's monthly JSON
endpoint, falling back to the quarterly fact sheet PDF, then your last cached
value, then manual entry. It never guesses silently.

## Limitations

- **No cost basis.** It cannot compute capital gains, and minimizes taxable
  trade *volume* as a proxy. Selling in a taxable account may realize capital
  gains or losses this tool never sees.
- **No costs.** Amounts exclude commissions, fees, bid-ask spreads and any
  short-term redemption fees, and it does not know your broker's fund minimums
  or trading restrictions. Prices move between the values you type and the
  price an order fills at.
- **The wash-sale check is a warning, not a guarantee.** It matches funds by
  the name you type, so two share classes of one index (VTI and VTSAX) are not
  recognized as the same security even though the IRS may treat them as
  substantially identical. Check your lots before placing the orders.
- **Preferring international in taxable is a rule of thumb.** The credit is
  worth a couple of basis points and is partly offset by those funds' higher,
  less-qualified dividends; the tool weighs neither.
- **It knows nothing about** contribution or withdrawal limits, holding
  periods, early-withdrawal penalties, or what a given account can actually
  hold -- a 401(k)'s fixed fund menu, for instance.
- **VT's allocation may be stale**, or the fetch may fail and fall back to a
  cached or manually entered value.

## Development

```bash
git clone https://github.com/melvinrajendran/three-fund-rebalance
cd three-fund-rebalance
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python -m three_fund_rebalance.cli   # run your working copy
pytest                               # test
ruff check three_fund_rebalance tests
```

Run it as a module rather than through the console script: that always
executes the copy you are editing, whatever else is on your PATH.

[CLAUDE.md](CLAUDE.md) is the contributor guide -- the solver's design, the
invariants that span files, testing conventions, and the wording the output
has to keep.

## License

[MIT](LICENSE).
