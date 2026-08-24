# Three-Fund Rebalance

An interactive CLI that computes the trades needed to rebalance a
[three-fund portfolio](https://www.bogleheads.org/wiki/Three-fund_portfolio)
(U.S. stocks / international stocks / bonds) across any number of accounts --
tax-deferred, tax-free and taxable alike -- putting each asset class where it
is taxed least.

## Disclaimer

**Not investment, tax, or legal advice, and not a recommendation to buy or
sell.** Consult a professional about your situation, and see the CLI's
[Limitations](#limitations).

## Example

Three accounts, a target of 80% stocks / 20% bonds, and a rebalancing band of
5 percentage points or 25% of an asset class's own target, whichever is
tighter:

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
    BND (bond fund)                          --
    Total                            $90,000.00

  Fidelity Roth IRA (Roth IRA, tax-free)
    VTI (U.S. stock fund)            $20,000.00
    VXUS (international stock fund)          --
    BND (bond fund)                          --
    Total                            $20,000.00

  Employer 401(k) (Traditional 401(k), tax-deferred)
    VTI (U.S. stock fund)            $30,000.00
    VXUS (international stock fund)          --
    BND (bond fund)                  $10,000.00
    Total                            $40,000.00

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

  Fidelity Roth IRA (Roth IRA)
    Exchange $5,600.00 from VTI to VXUS

  Employer 401(k) (Traditional 401(k))
    Sell $30,000.00 of VTI
    Buy $10,000.00 of VXUS
    Buy $20,000.00 of BND

If these orders fill at the values you entered, your portfolio will hold 49.6%
U.S. stocks, 30.4% international stocks, and 20.0% bonds.

Not investment, tax, or legal advice, and not a recommendation to buy or sell.
Consult a professional about your situation.
```

## Install

Requires Python 3.10+. Install with [pipx](https://pipx.pypa.io/), which puts
the CLI on your PATH in its own isolated environment:

```bash
pipx install git+https://github.com/melvinrajendran/three-fund-rebalance
```

No pipx? `brew install pipx && pipx ensurepath` on macOS, or `python3 -m pip
install --user pipx && python3 -m pipx ensurepath` elsewhere.
[uv](https://docs.astral.sh/uv/) does the same job and will fetch a suitable
Python if your system one is too old:

```bash
uv tool install git+https://github.com/melvinrajendran/three-fund-rebalance
```

To update, reinstall with `--force`. The version doesn't change on every
commit, so plain `pipx upgrade` sees the installed copy as current and skips
the refetch:

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

**Allocation before location.** The tool settles what each asset class should
be worth, then decides which accounts hold it.

**Rebalancing bands.** You set two -- and an asset class has to satisfy both,
so the tighter binds: the *absolute band*, in percentage points of the
portfolio, and the *relative band*, as a share of the asset class's own
target. Zero on either tolerates no drift. Both are asked outright with no
suggested answer; 5 and 25 -- the 5/25 rule -- is the usual convention.

**A band is a trigger, not a destination.** No trades while every asset class
is inside its band; once one falls outside, all three go back to target. Cash
is invested first, and the band is judged on what it leaves behind.

**Asset location.** Bonds fill tax-advantaged accounts first, tax-deferred
before tax-free, since their interest is taxed yearly as ordinary income.
International stocks prefer taxable, where the foreign tax withheld on them is
claimable as a credit a tax-advantaged account forfeits.

**Two things open a taxable trade: reaching the allocation, and moving bonds
out of taxable.** No capital-gains tax is realized inside a sheltered account,
so every preference below those only decides which funds an account already
being traded ends up holding.

**An account holds one target-date fund or all three individual funds, never
both** -- a U.S. stock fund, an international stock fund and a bond fund --
with cash alongside either. A target-date fund is then the account's only
holding, pinned by its total: it can only invest its cash.

**Name a fund you don't own yet.** All three individual funds are asked for
whether or not you hold any today, and one entered at $0 is capacity: the plan
can buy into it. That is often what lets a portfolio reach its bond target
without selling anything in a taxable account.

**Money never moves between accounts.** Each account's total is fixed; a
rebalance only reallocates within it, including investing its cash. Orders
smaller than $1.00 are left out as impractical.

**VT's U.S./international split** comes from Vanguard's monthly JSON endpoint,
falling back to the quarterly fact sheet PDF, then your last cached value,
then manual entry -- which points you at VT's fund page to read the number
off yourself. It never guesses silently.

## Limitations

**No cost basis.** It cannot compute capital gains, and minimizes taxable
trade *volume* as a proxy. Selling in a taxable account may realize capital
gains or losses this tool never sees.

**Every account holding individual funds is assumed able to buy all three.**
A plan with a restricted fund lineup -- a 401(k) with no international option,
say -- may be given an order it cannot fill.

**No costs.** Amounts exclude commissions, fees, bid-ask spreads and
short-term redemption fees, and it does not know your broker's fund minimums
or trading restrictions. Prices move between the values you type and the
price an order fills at.

**The wash-sale check is a warning, not a guarantee.** It matches funds by
the name you type, so two share classes of one index (VTI and VTSAX) are not
recognized as the same security even though the IRS may treat them as
substantially identical. Check your lots before placing the orders.

**Preferring international in taxable is a rule of thumb.** The credit is
worth a couple of basis points and is partly offset by those funds' higher,
less-qualified dividends; the tool weighs neither.

**It cannot tell a municipal bond fund from a taxable one.** Bonds are moved
into sheltered accounts on the assumption their interest is taxed as
ordinary income; a muni fund belongs in taxable, and this will move it out.

**All the cash you enter is invested.** There is no reserve: keep an
emergency fund or a spending reserve out of the amounts you enter.

**It knows nothing about** contribution or withdrawal limits, holding
periods, early-withdrawal penalties, or what a given account can actually
hold -- a 401(k)'s fixed fund menu, for instance.

**VT's allocation may be stale**, or the fetch may fail and fall back to a
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
