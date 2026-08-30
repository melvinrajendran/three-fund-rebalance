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

**A target-date fund is one position holding a fixed internal ratio,** not three
positions. `Holding.fraction_of` is what lets a single slot contribute fractionally
to all three targets, and it is stated once: `Holding.component` is it times the
value, and `rebalance._fund_type_coefficient` is it as the float the LP needs. It
used to be written out four times -- once per asset class as
`Holding.us_stock_component()` and friends, and again for the solver -- which is four
copies of one invariant and three chances for the report and the solver to disagree
about what an account holds.

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
- `TargetDateAllocation` allows the three percentages to sum to 100 ±
  `PERCENT_SUM_TOLERANCE`, because a fact sheet rounds each sleeve to a tenth. Read as
  literal percentages over 100, a fund printed 64.0 / 34.3 / 1.6 leaves a tenth of a
  percent of its account belonging to no asset class -- which the implied row would
  silently dump into bonds. `TargetDateAllocation.fraction_of` divides by the actual
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

**An account holds a target-date fund *or* individual funds, never both** (cash may
sit alongside either). `Account.__post_init__` enforces it, `prompts` asks which kind
up front instead of offering a fourth yes/no, and `INDIVIDUAL_FUND_TYPES` is the set
that clashes with `TARGET_DATE`.

**A declared holding is capacity, whatever it is worth -- and an account holding
individual funds declares all three.** A slot exists because the account *can* hold
that asset class, not because it currently does: `_build_slots` takes every non-cash
holding regardless of value, its LP bound is `(0, account total)`, and `report`
renders a zero one as `--` rather than `$0.00`. The model always allowed this; for a
long time the only way to reach it was to answer "yes" to "does this account hold a
bond fund?" and then type `0`, so the truthful answer removed the only place an asset
class could ever go. `prompts._prompt_fund_holdings` now asks for all three outright,
which also stops it re-asking what the kind question has just answered. The
assumption that comes with that -- every such account can buy all three -- is a README
limitation: a 401(k) with no international option may be handed an order it cannot
fill.

The reason it matters is that capacity is what the solver is short of. In the
README's own example the added slots are what let the whole bond target be reached
inside the shelters, so the taxable account is not touched at all; with the bond slot
missing from the Roth it had to sell there.

**A fund's name is asked immediately above its value, so the name prompt refuses an
answer the value prompt would have taken** -- `prompt_str`'s `reject_numeric`, passed
only from `_prompt_holding`. On a saved account the ticker arrives pre-filled and the
value is the only thing that changed quarter to quarter, which makes typing the new
value at the name prompt the natural slip; nothing else caught it, so the amount
became the fund's name, was saved to config.json, and came back in the plan as "Buy
$29,500.00 of 178000" -- the one path that produced a wrong order that looked right.
The test is `_parses_as_a_number`, i.e. *what the other question accepts*, rather than
a pattern of digits, so the two cannot drift apart: a value typed with a comma or a
dollar sign is not one of these and `prompt_decimal` would have rejected it too. Only
the fund prompts ask for it -- an account nickname sits next to no value question, and
no order is placed against it.

The consequence worth holding onto: **a target-date account has exactly one slot, so
the per-account budget equality pins it outright.** No objective can reach inside it.
That is what stops the solver from liquidating a taxable target-date fund to relocate
the bond sleeve within it -- which it used to do even for a portfolio already sitting
on its target. It also means such an account sets a *floor* under every asset class,
not just a ceiling, which is why `_asset_class_reach` returns both.
