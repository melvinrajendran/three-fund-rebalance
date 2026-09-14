# Invariants that span files

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

**Money is `Decimal`, never `float`.** The sole exception is inside the LP, which
necessarily works in floats; `rebalance._to_decimal` and `models.to_cents` convert
back at the boundary. Introducing a float into a dollar amount elsewhere is a bug.
The two crossings *into* float are `_fund_type_coefficient` and the `float(...)` calls
building constraint rows; anything coming back out -- `taxable_bond_dollars` included --
reads the Decimal from the holding rather than round-tripping a coefficient through
`Decimal(str(some_float))`.

**An asset class has one key, defined in one place.** The dicts of dollar amounts and
percentages that pass between `allocation`, `rebalance` and `report` are keyed by
`allocation.ASSET_CLASS_KEYS` -- and note bonds are `"bond"` there, not
`FundType.US_BOND.value` (`"us_bond"`, which is the *storage* spelling that goes into
config.json). Two of the three keys coincide with the enum values and the third does
not, which is exactly why the mapping is imported rather than re-typed: `_TARGET_KEYS`
in the solver and `_CATEGORY_TARGET_KEYS` in the report both derive from it. A reader
who infers the pattern from the first two is wrong about the third.

**A rebalance never moves money between accounts.** Each account's total value is an
equality constraint. Trades only reallocate *within* an account, including investing
that account's available cash.

**There are two shelters, not one.** `TaxTreatment` is three-valued: `TAXABLE`,
`TAX_DEFERRED` (traditional 401(k)/IRA, 403(b), 457(b), SEP, SIMPLE) and `TAX_FREE`
(Roth IRA, Roth 401(k), HSA). Both shelters are exempt today; they differ in what a
dollar of growth inside them is worth, which is what decides that bonds belong in
tax-deferred space and stocks in tax-free. Anything not `TAXABLE` is a shelter --
`Account.is_tax_advantaged()` -- and every taxable-vs-sheltered test in the solver is
written against `TAXABLE` so that adding a third shelter kind would not silently
change phases 1, 2 or 5.

**What to hold is decided before where to hold it, and never by it.**
`_resolve_allocation` settles the three asset-class totals first, honoring the
rebalancing band; the solver then hits those as hard equalities. Every location
objective is phrased as "minimize this asset class in that kind of account", which
means *relocate* only while the class total is fixed -- see [`solver.md`](solver.md),
where letting the band reach those phases turned out to be a real bug.

**`FundType.CASH` has an implicit target of zero** -- cash is always fully invested.
It is excluded from the tradeable slots and from `_TARGET_FUND_TYPES`.

**Cash is therefore not an asset class for drift purposes either.** It sits in the
portfolio total the three classes are measured *against* -- so a dividend swept into
an account dilutes all three at once -- but `_current_asset_class_dollars` never
counts it as one of them, and it has no band. What it does is trip
`_resolve_allocation`'s gate, which sends it through `_place_cash`; the band is then
asked about the portfolio the cash leaves behind. The user is asked for "Cash
available to invest", and every dollar of it is spent, so a reserve the user does
not intend to invest must simply not be entered -- a README limitation, not
something the solver can see.

**A multi-asset fund is one position holding a fixed internal ratio,** not three
positions. `Holding.fraction_of` is what lets a single slot contribute fractionally
to all three targets, and it is stated once: `Holding.component` is it times the
value, and `rebalance._fund_type_coefficient` is it as the float the LP needs. It
used to be written out four times -- once per asset class as
`Holding.us_stock_component()` and friends, and again for the solver -- which is four
copies of one invariant and three chances for the report and the solver to disagree
about what an account holds.

It is called *multi-asset* and not *target-date* because the mix is the user's, not
the fund's dated glide path: a 60/40 balanced fund, a LifeStrategy fund and a 2050
fund are all one position with three sleeves, and only the last of them is dated. A
target-date fund is still the commonest example, which is why it is the one the
cautions below reach for.

**The LP must never over-determine the portfolio total.** Each account spends
exactly its own total, and each asset class hits exactly the figure
`_resolve_allocation` settled on. Every slot's three class coefficients sum to 1, so
adding the three class rows together reproduces the account rows -- the third class
equality is *implied*, and stating it anyway is not free. An implied row is
satisfiable only if the two sides agree to the last bit, which floating point will
not do: coefficients summing to 1 + 1e-16 make a portfolio infeasible outright once
it is large enough for that relative error to exceed HiGHS's absolute feasibility
tolerance, somewhere below $8B. So `compute_trades` states `_TARGET_FUND_TYPES[:-1]`
and lets the budgets imply bonds, where the same error misplaces a billionth of a
cent instead. Do not "complete" that loop.

Two things feed it, and both shipped as infeasible portfolios reported to the user as
"no arrangement of the funds you hold reaches your target":

- `_to_decimal` rounds each class to six decimal places independently, so two classes
  rounding down half a micro-dollar each leave the three a millionth of a dollar short
  of the portfolio. `_reconcile_to_total` closes that at every exit of
  `_resolve_allocation` (the `dict(current)` return, the `_place_cash` return and the
  LP's own), putting the residue on the largest class. With the third row implicit
  this no longer decides feasibility, only whether the implied class lands on its
  resolved figure or a hair off -- but three amounts that do not add up to the
  portfolio are not "what each asset class should be worth", which is the function's
  whole contract. About one realistic portfolio in seven tripped this.
- `FundAllocation` allows the three percentages to sum to 100 ±
  `PERCENT_SUM_TOLERANCE`, because a fact sheet rounds each sleeve to a tenth. Read as
  literal percentages over 100, a fund printed 64.0 / 34.3 / 1.6 leaves a tenth of a
  percent of its account belonging to no asset class -- which the implied row would
  silently dump into bonds. `FundAllocation.fraction_of` divides by the actual
  sum instead, and is the **one** place the three sleeves become fractions:
  `Holding.fraction_of` delegates to it, `Holding.component` is that times the value,
  and `rebalance._fund_type_coefficient` is `Holding.fraction_of` as a float -- so the
  report, `_current_asset_class_dollars` and the solver all read a holding the same
  way by construction rather than by agreement. It normalizes the derived view only;
  the entered percentages are stored and echoed back untouched.

Nothing may assume the normalized fractions sum to exactly 1 -- as Decimals they leave
an artifact around 1e-28, as floats around 1e-16, and CPython 3.12's compensated
`sum()` hides the latter where 3.10's plain addition does not. That is why
`_resolve_allocation`'s uninvested-cash gate reads `to_cents(...)`: taken literally,
Decimal dust would count as cash and send a portfolio sitting on its target through
`_place_cash` for nothing. Cents are the grid money is entered and traded on, and
sub-cent cash is under `MIN_TRADE_DOLLARS` regardless.

**An account holds any combination of funds.** Any number dedicated to a single
asset class, any number of multi-asset funds, in any mix, with cash alongside.
`Account.__post_init__` enforces only what is left: one cash balance, and fund names
unique within the account.

It used to hold *either* one target-date fund *or* all three individual funds, and
`prompts` asked which up front. That was never a fact about portfolios -- a 401(k)
holding a 2050 fund beside an S&P 500 fund is an ordinary lineup and was unenterable
-- it was what made a target-date account's single slot pinned by its own budget row,
which is what stopped the solver liquidating one to relocate the sleeves inside it.
That guarantee is gone, deliberately: a multi-asset fund declared beside other funds
*is* tradeable, which is the point of declaring it that way, and phase 1 will sell a
taxable one to move its bond sleeve into a shelter. What survives of the old
protection is `rebalance._foreign_credit_coefficient`, which scores a fund that is
not majority-foreign at zero so no objective can reach inside one chasing a credit it
cannot pass on. See [`solver.md`](solver.md).

**A fund's name is unique within its account**, compared the way
`rebalance._normalized_fund_name` compares them -- case and surrounding space are
noise a user shouldn't have to get right. That name is the key an order is placed
against, the key `report.allocation_after_trades` applies a trade by, and the label
the report's rows are read by; two holdings sharing it make all three ambiguous at
once. Two funds of the *same asset class* under different names are fine and
ordinary: VTI beside VOO is two securities. Two with the same asset mix leave every
location phase tied across every split between them, and which one a purchase lands
in is deliberately unspecified -- see [`solver.md`](solver.md).

**A fund name maps to one kind and mix across every account.** VBIAX in a Roth and
VBIAX in a brokerage account are one security, so they cannot hold different things;
only the value is per account. Letting each holding carry its own copy of the details
is what let two copies come to disagree, so they are kept once: `models.FundCatalog`
holds one `FundProfile` per `fund_name_key`, the config file stores them once under
`"funds"` (a holding there is only a name and a value -- see
[`persistence.md`](persistence.md)), and every `Holding` the flow builds is read from
it. `FundCatalog.resolve` is what turns a change made in one account into a change in
all of them, and seeding a catalog with one fund described two ways raises rather than
picks. `Holding` still carries `fund_type` and `allocation` in memory, because the
solver and report read them there -- the rule is that nothing sets them except the
catalog.

**A declared fund is capacity, whatever it is worth -- and only a declared fund is
ever traded.** A slot exists because the account *can* hold that fund, not because it
currently does: `_build_slots` takes every non-cash holding regardless of value, its
LP bound is `(0, account total)`, and `report` renders a zero one as `--` rather than
`$0.00`. The model always allowed this; for a long time the only way to reach it was
to answer "yes" to "does this account hold a bond fund?" and then type `0`, so the
truthful answer removed the only place an asset class could ever go.

The fix was once to ask for all three outright. With the fund list now the user's
own, it is `prompts.FUND_EXPLANATION` instead: "Enter every fund this account can buy
or sell -- only these can be traded. A $0 position is fine." Both halves are
load-bearing. The first says what a fund left out costs, which is no longer merely a
missing row; the second grants permission to name a fund not yet bought, which is the
thing a reader hesitates over.

The old rule's assumption -- that every such account can buy all three -- is gone with
it, and so is the README limitation it carried. The exposure runs the other way now:
an account whose funds the user under-declares has less capacity than it really has,
and `_capacity_notes` is what says so.

The reason it matters is that capacity is what the solver is short of. In the
README's own example the declared-but-empty slots are what let the whole bond target
be reached inside the shelters, so the taxable account is not touched at all; with
the bond slot missing from the Roth it had to sell there.

**A fund's name opens the block its value closes, so the name prompt refuses an
answer the value prompt would have taken** -- `prompt_str`'s `reject_numeric`, passed
only from `_prompt_holding`. On a saved fund the ticker arrives pre-filled and the
value is the only thing that changed quarter to quarter, which makes typing the new
value at the name prompt the natural slip; nothing else caught it, so the amount
became the fund's name, was saved to config.json, and came back in the plan as "Buy
$29,500.00 of 178000" -- the one path that produced a wrong order that looked right.
The test is `_parses_as_a_number`, i.e. *what the other question accepts*, rather than
a pattern of digits, so the two cannot drift apart: a value typed with a comma or a
dollar sign is not one of these and `prompt_decimal` would have rejected it too. Only
the fund prompts ask for it -- an account nickname sits next to no value question, and
no order is placed against it. The kind question now sits between the name and the
value, which makes the slip less likely and the check no less worth having.

The consequence worth holding onto: **an account holding exactly one fund has exactly
one slot, so the per-account budget equality pins it outright.** No objective can
reach inside it. An account whose only holding is a multi-asset fund therefore sets a
*floor* under every asset class and not just a ceiling, which is why
`_asset_class_reach` returns both -- and it is the only thing that can still set a
non-zero one, since an account holding two or more funds has a coefficient running
down to whatever its smallest is.

Note this is a property of the *account* now, not of the fund. The same multi-asset
fund with a second fund beside it is traded like anything else.
