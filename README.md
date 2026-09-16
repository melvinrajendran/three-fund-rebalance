# Three-Fund Rebalance

An interactive CLI that computes the trades needed to rebalance a
[three-fund portfolio](https://www.bogleheads.org/wiki/Three-fund_portfolio)
(U.S. stocks / international stocks / bonds) across any number of accounts --
tax-deferred, tax-free and taxable alike -- preferring to hold each asset
class where it is taxed least.

## Disclaimer

**Not investment, tax, or legal advice, and not a recommendation to buy or
sell.** Consult a professional about your situation, and see the CLI's
[Limitations](#limitations).

## Example

Three accounts, a target of 80% stocks and 20% bonds, and a rebalancing band
of 5 percentage points or 25% of an asset class's target, whichever is
smaller:

```
Target Asset Allocation
-----------------------
  U.S. stocks           49.6%
  International stocks  30.4%
  Bonds                 20.0%

  Derived from 80% stocks and 20% bonds, with stocks split based on VT's 62%
  U.S. allocation (as of June 30, 2026).

Rebalancing Bands
-----------------
Plus or minus 5 percentage points, or 25% of an asset class's target,
whichever is smaller:

  U.S. stocks           44.6% to 54.6%
  International stocks  25.4% to 35.4%
  Bonds                 15.0% to 25.0%

No trades while every asset class stays within its band. If any asset class
drifts outside its band, all three are rebalanced back to their targets.

Account Holdings
----------------
  Vanguard Brokerage (Brokerage, taxable)
    VTI (U.S. stock fund)            $60,000.00
    VXUS (international stock fund)  $30,000.00
    BND (bond fund)                          --
    Total                            $90,000.00

  Vanguard Roth IRA (Roth IRA, tax-free)
    VTI (U.S. stock fund)            $20,000.00
    VXUS (international stock fund)          --
    BND (bond fund)                          --
    Total                            $20,000.00

  Employer 401(k) (Traditional 401(k), tax-deferred)
    VTI (U.S. stock fund)            $30,000.00
    VXUS (international stock fund)          --
    BND (bond fund)                  $10,000.00
    Total                            $40,000.00

Current vs. Target Allocation
-----------------------------
Total portfolio value: $150,000.00

                                    Current              Target  Absolute Drift  Relative Drift
  U.S. stocks           $110,000.00 (73.3%)  $74,400.00 (49.6%)     +23.7 pts *        +47.8% *
  International stocks   $30,000.00 (20.0%)  $45,600.00 (30.4%)     -10.4 pts *        -34.2% *
  Bonds                  $10,000.00  (6.7%)  $30,000.00 (20.0%)     -13.3 pts *        -66.7% *

  * outside its rebalancing band

Orders to Place
---------------
Review each order before placing it:

  Vanguard Roth IRA (Roth IRA)
    Exchange $5,600.00 from VTI to VXUS

  Employer 401(k) (Traditional 401(k))
    Sell $30,000.00 of VTI
    Buy $10,000.00 of VXUS
    Buy $20,000.00 of BND

  If these orders fill at the values entered here, the portfolio will hold
  49.6% U.S. stocks, 30.4% international stocks, and 20% bonds.

Notes
-----
International stocks in tax-advantaged. Buying $15,600.00 of international
stocks in tax-advantaged accounts gives up a foreign tax credit. Buying them
in a taxable account would have meant triggering a taxable sale.
```

## Installation

### Prerequisites

[uv](https://docs.astral.sh/uv/), which puts the CLI on your PATH in its own
isolated environment and fetches a suitable Python if your system one is older
than 3.10:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install

```bash
uv tool install three-fund-rebalance
```

Or run it once without installing anything:

```bash
uvx three-fund-rebalance
```

### Update

```bash
uv tool upgrade three-fund-rebalance
```

### Uninstall

```bash
uv tool uninstall three-fund-rebalance
```

The saved portfolio lives outside the installation at
`~/.three_fund_rebalance/config.json`, so updating -- or uninstalling -- never
touches it. Files written by older versions are upgraded in place on the next
save.

## Running

```bash
three-fund-rebalance
```

Three numbered steps -- the target asset allocation, when to rebalance, the
account holdings -- then the summary above. Run it again and every saved
account comes back with its last values pre-filled: press Enter to keep one or
type a new one.

To re-run a portfolio that has not changed, `--no-input` skips the three steps
entirely and prints the summary from the saved file. It never writes that file
back, so the "last saved" date keeps meaning the last time you entered a
figure. It pairs with `--write-summary` for an unattended run, and errors
rather than guessing if the saved portfolio is missing something.

| Flag | Effect |
| --- | --- |
| `--config PATH` | Portfolio file to read and write (default `~/.three_fund_rebalance/config.json`) |
| `--fresh` | Ignore the saved portfolio and start blank |
| `--no-input` | Use the saved portfolio as-is: ask nothing, print the summary, and don't save |
| `--no-save` | Don't offer to save this run's answers to the portfolio file |
| `--offline` | Skip the live VT fetch; use the saved or a manually entered value instead |
| `--vt-us-pct PCT` | Set VT's U.S. stock allocation % directly, skipping the lookup and the prompt |
| `--write-summary [PATH]` | Write the summary to PATH, or with no PATH to a timestamped file beside the portfolio file |
| `--version` | Print the installed version and exit |

## How It Works

**The U.S. and international split** comes from the Vanguard Total World Stock
ETF (VT): Vanguard's monthly JSON endpoint, falling back to the quarterly fact
sheet PDF, then the last saved value, then manual entry, which points you at
VT's fund page to read the number off yourself. It never guesses silently.

**Rebalancing bands.** You set two -- and an asset class has to satisfy both,
so the smaller binds: the *absolute band*, in percentage points of the
portfolio, and the *relative band*, as a percentage of the asset class's
target. They default to 5 points and 25%, and zero on either tolerates no
drift.

**A band is a trigger, not a destination.** No trades while every asset class
stays within its band; if any drifts outside, all three are rebalanced back to
their targets. Cash is invested first, and the band is judged on what it
leaves behind.

**An account holds any combination of funds.** Each one either holds a single
asset class or is a *multi-asset fund* -- a target-date, balanced or
lifestrategy fund -- whose mix of the three you enter from its fact sheet. A
2050 fund beside an index fund, two bond funds, cash alongside: all ordinary.

**Enter every fund the account can trade, including ones you hold none of.**
Only the funds you enter can be bought or sold, so one entered at $0 is
capacity the plan can buy into -- often what lets a portfolio reach its bond
target without selling anything in a taxable account -- and one left out is a
place the plan cannot put an asset class.

**Allocation before location.** Two solves, in order. The first settles what
each asset class should be worth -- as close to target as the accounts allow,
then moving least, then sharing what they cannot reach in proportion to each
class's band. The second decides which accounts hold it, never the reverse.

**Both stages are linear programs.** Every variable is a dollar amount -- one
per asset class in the first solve, one per fund position per account in the
second. Equalities pin what cannot move, each preference is a cost to
minimize, and the answer is an exact optimum, not an approximation.

**Money never moves between accounts.** Each account spends exactly its own
total, so a rebalance only reallocates within one, including investing its
cash.

**Preferences are ranked, not weighted.** Each is optimized in turn and its
result frozen as a constraint on the next, so a lower priority can never cost
anything on a higher one. There is no exchange rate between a basis point of
foreign tax credit and a dollar of realized gain, and nothing here invents
one. The second solve ranks six:

1. Move bonds out of taxable accounts, since their interest is taxed yearly
   as ordinary income.
2. Trade as little as possible inside taxable accounts.
3. Avoid buying, in a sheltered account, a fund being sold in a taxable one.
4. Among shelters, hold bonds in tax-deferred space, which by common
   convention leaves tax-free accounts for stocks.
5. Hold a mostly-foreign fund in taxable, where the foreign tax withheld on
   it is claimable as a credit a sheltered account forfeits. A fund has to be
   more than half foreign to pass that credit on at all, so a target-date fund
   does not count.
6. Trade as little as possible everywhere else.

Reaching the settled allocation is a hard constraint, so it will sell in a
taxable account if the sheltered ones cannot absorb the change. Of the six
only the first can; 3 through 6 rearrange sheltered accounts and choose which
fund an account already being traded ends up in.

## Limitations

**VT's allocation may be stale**, or the fetch may fail and fall back to a
saved or manually entered value.

**A fund you don't enter is one the plan cannot use.** Leaving a fund out of
an account -- or not naming one you could buy but don't hold yet -- gives that
account less room than it really has, and the plan works around capacity it
was never told about.

**All the cash you enter is invested.** There is no reserve: keep an
emergency fund or a spending reserve out of the amounts you enter.

**The orders may not hit the target exactly.** A multi-asset fund's mix is
fixed, so an account holding one and nothing else is pinned to that mix -- its
bond sleeve counts against even a 0% bond target. The plan gets as close as
the accounts allow, and says which asset class fell short and by how much.
Orders under $1.00 are also left out as impractical, and counted.

**Preferring international in taxable is a rule of thumb.** The credit is
worth a couple of basis points and is partly offset by those funds' higher,
less-qualified dividends; the tool weighs neither. Whether a fund actually
passes the credit through depends on its holdings at year end, which the mix
you enter only approximates.

**It cannot tell a municipal bond fund from a taxable one.** Bonds are moved
into sheltered accounts on the assumption their interest is taxed as
ordinary income; a muni fund belongs in taxable, and this will move it out.

**No cost basis.** Trading less in a taxable account is a stand-in for
realizing less tax, not a calculation of it. Selling there may realize capital
gains or losses this tool never sees.

**The wash-sale note is a flag, not a guarantee.** It matches funds by
the name entered, so two share classes of one index (VTI and VTSAX) are not
recognized as the same security even though the IRS may treat them as
substantially identical. Check the lots before placing the orders.

**Nothing that happens at the broker is modeled.** Amounts exclude
commissions, fees, bid-ask spreads and short-term redemption fees, and the
tool knows nothing of your broker's fund minimums or trading restrictions, or
of contribution and withdrawal limits, holding periods and early-withdrawal
penalties. Prices move between the values entered and the price an order fills
at.

## Development

```bash
git clone https://github.com/melvinrajendran/three-fund-rebalance
cd three-fund-rebalance
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python -m three_fund_rebalance.cli   # run your working copy
pytest                               # test
pytest -m network                    # plus the live VT sources
ruff check three_fund_rebalance tests
```

Run it as a module rather than through the console script: that always
executes the copy you are editing, whatever else is on your PATH.

## License

[MIT](LICENSE).
