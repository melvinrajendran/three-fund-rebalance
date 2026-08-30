# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# One-time setup (python3 on macOS may still be the system 3.9; use an explicit
# interpreter such as python3.12 if so)
python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"

pytest                                   # full suite (fast: ~0.4s, no network)
pytest tests/test_rebalance.py           # one file
pytest tests/test_cli.py::TestArgParsing::test_version_flag_prints_version_and_exits_cleanly
pytest -k "target_date and not persistence"   # by expression
pytest -m network                        # opt-in: hits the live VT sources
pytest --cov=three_fund_rebalance --cov-report=term-missing

ruff check three_fund_rebalance tests    # lint (CI runs exactly this)
ruff check --fix <path>                  # autofix
```

Re-run `pip install -e ".[dev]"` only when packaging metadata changes (a new/bumped
dependency, a renamed console script, a new top-level package) -- new modules inside
`three_fund_rebalance/` take effect immediately under the editable install.

### Running the CLI without side effects

The interactive flow reads and writes `~/.three_fund_rebalance/config.json` and hits
the network. To exercise it safely:

```bash
python -m three_fund_rebalance.cli --offline --vt-us-pct 62 --fresh --no-save --config /tmp/scratch.json
```

Always invoke it as a module, never through the `three-fund-rebalance` console
script: that runs the working copy regardless of what else is on PATH, so a uv
or pip install of the published package can never be what you actually tested.

To see rendered output non-interactively, drive `run()` with a scripted prompter
rather than piping stdin -- see "Testing conventions" below. Piping to stdin is
brittle because the prompt sequence branches on answers.

## Architecture

One linear pipeline, orchestrated end-to-end by `cli.run()`:

```
load_config → prompt_stock_bond_allocation → resolve_vt_allocation
            → prompt_rebalance_band → prompt_accounts
            → ⟳ compute_target_allocation → compute_trades → format_report → _revise
            → _offer_summary_file → save_config
```

The one loop in it is `⟳`, and it exists because **a typo is noticed in the
report and nowhere earlier**. A wrong balance surfaces as an implausible order,
a wrong ticker in Account Holdings, a wrong band as "no trades needed" -- none
of them visible at the prompt that collected them, so a confirmation gate ahead
of the solve would be asking "is this right?" before printing the only thing
that answers it. `_revise` re-asks exactly one answer and the loop recomputes;
`_Answers` is the mutable carrier that makes "one answer" possible, since
`RebalanceInputs` is frozen and rebuilt each pass. A `RebalanceError` enters
the same loop rather than exiting, because an unplannable portfolio is usually
a mistyped one; declining the offer is what still returns 1.

The menu carries `NOTHING_TO_UPDATE` ("No Updates, Continue") last -- the way out for a mind changed one
question later. Last rather than first because reaching this menu means having
already answered yes to updating something, so it is the change of mind and not
the expected answer; "No Updates" answers "What would you like to update?" in
the words the question asked it, and the second half says what happens next
because every other entry visibly leads somewhere. Choosing it ends the loop rather than asking the yes/no
again. Past a *failed* solve it is a decline instead, and returns 1: there is no
plan to go on to, and nothing has changed to make the next attempt differ from
the one that just failed. That path is also why the `except` clause clears
`inputs` and `result` -- the previous pass's plan does not describe this pass's
answers, so carrying it forward would report and save a plan for a portfolio
that no longer exists.

The menu names each question by `prompts`'s subheading constants
(`STOCK_BOND_SUBHEADING` and friends) rather than by a paraphrase, and `cli`
prints its headings from the same constants -- so "go back to that question"
and the heading it goes back to cannot come to disagree. The VT entry is
omitted when `--vt-us-pct` supplied the split, since re-asking it could only
offer to contradict the invocation. Everything the menu can reach is re-asked
by the *same* function step 1, 2 or 3 used: `prompt_add_accounts` and
`prompt_revise_account` were split out of `prompt_accounts` for that reason,
and `prompt_accounts` now calls them, so there is one implementation and not
two that drift.

Modules map to stages: `persistence` ↔ config file, `prompts` ↔ all input,
`vt_allocation` ↔ the one network call, `allocation` ↔ percentages→dollars,
`rebalance` ↔ the solver, `report`/`formatting` ↔ output. `config.py` holds
constants only; `models.py` holds the dataclasses everything else passes around.

The imports form a DAG with `models` at the bottom and `cli` at the top, and it is
worth keeping that way. In particular **`report` does not import `rebalance`**: it
renders a `RebalanceResult`, which lives in `models` alongside `Trade` for exactly
that reason, so the reporting layer depends on the data and not on the scipy-backed
solver that produced it. (`report.py` must not import `prompts.py` either -- shared
presentation constants live in `formatting.py`.)

The user walks **three** numbered steps -- target allocation, rebalancing band,
account holdings. The report is not a fourth: it is what those produce, so it gets
`format_result_header` (same `=` rule, no "STEP x OF y") rather than a step banner.
`cli._INPUT_STEPS` is the count, in one place.

### Invariants that span files

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
means *relocate* only while the class total is fixed -- see the solver section, where
letting the band reach those phases turned out to be a real bug.

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

### The solver (`rebalance.py`)

Two stages. **`_resolve_allocation` decides what each asset class should be worth**,
then six phases decide where to hold it.

**The band is a trigger, not a destination.** The first stage opens with a gate: with
nothing uninvested it is plain `Decimal` and no solve at all -- every class inside its
band returns `dict(current)` outright, "leave it alone" answered with the numbers the
portfolio already holds rather than with a solver's slack spent drifting a fraction of
a cent. Anything that trips the gate sends the whole portfolio back to target, all
three classes, not just the one that breached.

Stopping at the nearest band edge instead is what this did once, and it was worse on
two counts. It leaves the portfolio *on* the boundary, so the next small drift trips
the band again -- rebalancing to the centre buys a full band's worth of quiet. And it
is under-determined: a class one point out of band can be brought back by selling
either of the other two at identical cost, so which one got sold came down to
whichever vertex HiGHS happened to return.

**The gate reads the band `_reachable_bounds` has widened**, not the band the user set.
A class the accounts pin outside its band -- a target-date fund's bond sleeve against a
0% bond target -- is outside it forever, and a band that can never be satisfied is a
band that never says "leave it alone": every later run would drive all three classes
back to exact target and trade on any drift at all, which is the opposite of what a
band is for. Widening only ever happens where the band and the reach do not overlap,
and it cannot wave through a portfolio that could still do better: `reach`'s floor is a
valid lower bound on every achievable total, so the only current value inside the
widened region is one already sitting on that bound. The run that gets a pinned class
as close as it can go is the last run that trades. The *report* keeps reading the band
as set -- that is the user's policy, and the comparison table still marks the class
outside it.

**Cash is handled first, and the band is then asked about what it leaves behind** --
not about the portfolio still holding it. That is `_place_cash`: a separate tiny LP
constrained to `p >= current`, which is what "spend the cash and sell nothing" means,
since only a sale can shrink a class total. It answers with the resulting totals when
the cash alone leaves every class inside its band, and `None` when it does not, which
is `_resolve_allocation`'s signal to rebalance properly.

It is lexicographic too, and the order is the other way round from its caller's: (1)
minimize how far outside its band each class is left; (2) among the ties, sit as close
to target as possible. The band comes first because it is what the answer turns on --
with two classes below target the cash could go to either and be equally close to
target overall, while only one of those choices might get the laggard back inside its
band.

**Testing the cash itself instead is a bug that shipped and was caught in real use.**
Treating any uninvested balance as grounds to reopen the question meant a few cents
swept up from a dividend rebalanced a portfolio that was comfortably inside its band on
all three classes -- thousands of dollars of trades, taxable sales included, over a
quarter of a percent of the portfolio.
`test_cash_alone_does_not_trigger_a_rebalance` is the guard, and it is written on the
deterministic path: the cash reaches the laggard through a free swap in a shelter, so
the taxable account only ever buys.

Past the gate it is a tiny LP over three variables, lexicographic: (1) sit as close to
target as the accounts allow; (2) among the ties, move as little as possible from where
the portfolio already sits; (3) among *those* ties, share the shortfall the accounts
cannot avoid, so no class is left disproportionately far outside its own band. (2) and
(3) run only when (1) comes back nonzero, i.e. only when
the exact target is unreachable -- an account holding a single fund pins that fund's
share of the portfolio, and the closest reachable points are then a whole face rather
than a vertex. With U.S. stock pinned at 60% against a 50/25/25 target, every split of
the remaining 40% is exactly as far from target as every other;
`test_an_unreachable_target_settles_nearest_to_where_the_portfolio_sits` fails without
(2), which is what earns it its keep. Its bounds are `reach` and nothing else -- the
band has had its say at the gate, and constraining these to it as well is what used to
turn a target the accounts cannot quite reach into a refusal to plan at all.

**(3) exists because (2) goes flat too, and in a way that is easy to miss.** When every
class sits on the *same* side of its target -- which a target-date fund's bond sleeve
against a 0% bond target produces on every run -- a dollar given to any class closes the
total gap by exactly a dollar and moves the portfolio by exactly a dollar, so both
objectives above tie across the whole face and the split comes down to whichever vertex
HiGHS returns. That is a plan that can change under a scipy upgrade with nothing in the
portfolio having moved.

Two things about how it measures, and both are load-bearing:

- **It shares the *excess*, not the drift** -- `_unavoidable_drift` is each class's
  distance from target after the target is clamped to `reach`, and the objective ranks
  what is left over. Measured raw, a pinned class is the largest drift in the portfolio
  by construction, so minimizing the largest drift is satisfied by that class alone and
  leaves the others tied exactly as they were. That was prototyped and changed nothing
  whatsoever; `test_a_class_pinned_away_from_target_does_not_absorb_the_tie_break` is
  the guard.
- **It measures in band-widths, not dollars**, which is the 5/25 rule's own answer to
  whether absolute or relative drift is the one that counts. The band is already the
  tighter of the two, per class, so normalizing by it inherits that ruling rather than
  adding a second one that could contradict it. Where the bands are equal -- any two
  targets at or above 20% -- it reduces to sharing the dollars evenly, so the
  distinction only shows up when a target straddles 20%.
  `test_the_shortfall_is_shared_in_band_widths_and_not_in_dollars` fails against the
  dollar version, which is what earns it its keep.

The row is written as `excess <= band * m` rather than `excess / band <= m`: multiplied
through it needs no division, and **a band of zero states exactly what a band of zero
means** -- the row collapses to `d <= floor`, pinning that class to the best drift it
can reach. A 0% target gives one, so this is not a corner case. It is also the one row
here that could in principle be unsatisfiable alongside the others, which is why the
solve is wrapped in `suppress(RebalanceError)`: a tie-break is a refinement, never a
requirement, and (2)'s answer is already a good one.

**(3) carries (2)'s bound like every other rank.** Dropping `A_ub.append(stay_put)` does
not merely loosen it -- it lets the tie-break *override* "move as little as possible",
which is a different policy and a visibly worse one, trading to even out a shortfall the
portfolio was already sitting closer to. Three existing tests fail without that bound,
`test_an_unreachable_target_stops_at_what_the_accounts_can_hold` among them.

Note the tenth column `m` is added to this LP alone, by widening its rows in the branch
that needs it, rather than by raising `_ALLOCATION_WIDTH` under both allocation LPs --
`_place_cash` shares the nine columns and has no use for a tenth. And phase 6 is
indifferent to the whole thing: sharing a purchase across two funds moves the same
dollars, which is all that phase measures.

**Skipping that stage and handing the band to the six phases below is a bug that was
shipped once and caught in real use.** Given a portfolio inside its band but with
international parked in a Roth and a taxable account too full to take any, phase 5
could not relocate -- so it satisfied itself by *selling* international and buying U.S.
stock up to the band ceiling, while phase 4 liquidated a bond fund the portfolio was
already several points underweight. Both objectives are stated as "minimize this asset
class in that kind of account", which only means "relocate it" while the class total
is fixed. `TestAllocationIsSettledBeforeLocation` pins the whole shape.

The six location phases then run over one shared variable layout, against the resolved
totals as hard equalities -- each phase's optimum carried forward as a `<=` bound so
later phases refine but never undo earlier ones:

1. Minimize bonds left in taxable accounts (fill sheltered room first).
2. Minimize trade volume *within taxable accounts* -- the proxy for avoiding capital
   gains. **No cost-basis data is collected**, so this is an approximation, not a
   gains calculation. Do not describe it as one in user-facing text.
3. Minimize wash-sale exposure: dollars bought, in a shelter, of a fund also held in
   a taxable account.
4. Minimize bonds held in *tax-free* accounts, i.e. put them in tax-deferred space
   and leave Roth/HSA room for stocks.
5. Minimize the international fund held in *tax-advantaged* accounts, i.e. prefer it
   in taxable, where its foreign withholding is claimable as a credit.
6. Tie-break by minimizing total trade volume everywhere, so the plan
   moves the fewest dollars. **Dollars, not positions** -- an account's total is
   an equality, so its buys equal its sells and the volume is twice what is sold
   however many funds the buy side is split across. Splitting one purchase into
   two is free here, which is what lets the allocation stage's third objective
   do it.

The variable layout is `[ x (n) | y (n) | w (k) ]` -- slot values, their absolute
deviations `|x - current|`, and one-sided purchase amounts for phase 3. It is built
once, so an objective is a vector over the same columns and a solved optimum is a row
appended to `A_ub`. An objective with no nonzero coefficient is skipped rather than
solved: it would only re-find a feasible point and carry a vacuous bound.

The six objectives themselves live in `_location_objectives`, apart from the
constraint rows -- they are pure data (a cost vector and the sentence `_solve` prints
if that phase is infeasible), and the ranking *is* the design, so it reads better as
a list than as forty lines wedged mid-function. Keep the constraint-row construction
inside `compute_trades` though: the point of the shared layout is that every phase
indexes the same columns, and splitting that across functions hides it.

The two *allocation*-stage LPs (`_resolve_allocation` and `_place_cash`) share their
own smaller layout, three columns per class: `[ p | first anchor | second anchor ]`
(plus, in `_resolve_allocation`'s third objective alone, a tenth column widened on
locally -- see above).
What the anchors measure differs by caller, but the shape does not, which is what
lets both build rows through `_allocation_row` and `_abs_value_rows` instead of a
dozen hand-written `row[3 + index]` expressions whose only documentation was being
read carefully.

`_slot_indices_by_account` groups the slots once for the three places that need them
(the capacity check, the budget rows, the rounding pass). Each of those used to
rescan the whole slot list per account. Nothing here is ever big enough for that to
be slow -- the LP solve dominates by orders of magnitude -- but one of the three
already grouped properly, and having the other two disagree read as an oversight.

**Everything below phase 2 is free-rearrangement only.** Phases 3, 4 and 5 can decide
*which* fund an account being traded anyway should end up in, and can rearrange
sheltered accounts at will, but none of them can open a taxable trade. That ranking is
the whole design: the credit phase 5 chases is worth a couple of basis points against
a realized gain we cannot even measure, and the same logic governs 3 and 4.

Some finer points that are easy to undo by accident:

- **Phase 5 tests `slot.fund_type` directly** rather than going through
  `_fund_type_coefficient`. A target-date fund is not majority-foreign, so it passes
  no credit through from either kind of account, and counting its international sleeve
  makes the solver liquidate half a TDF for nothing
  (`test_target_date_international_sleeve_is_left_alone` pins this).
- **Phase 5 losing is disclosed, and it is the only phase whose loss is.**
  `_international_location_notes` fires when the plan *buys* international stock in a
  tax-advantaged account, which is phase 2 outranking phase 5 -- the one place the plan
  visibly contradicts what the README says it optimizes for, and it read as a bug in
  real use. It fires on the buy rather than on the residue for the reason
  `_taxable_sale_note` fires only on a sale: international already sitting in a shelter
  is the common case and reports nothing worth reading, so a note that fired on it
  would fire nearly every run. Silent with no taxable account, where there is no
  alternative to describe, and it reads `trade.fund_type` for the same reason phase 5
  reads `slot.fund_type` -- a target-date sleeve is not what it is about. **It says
  what the alternative would have cost, never where the phase sat**, because a note is
  read by someone holding a plan and not by someone holding this file. It shipped once
  as "Avoiding a taxable sale ranks higher", which names an ordering the reader has
  never seen; "Buying them in a taxable account would have meant triggering a taxable
  sale" is the same fact in the only terms available to them, and it names the cost as
  the report's own "Taxable sale" note names it rather than as a vaguer "selling
  something there". The label is "International stocks in tax-advantaged": the plural
  is the asset class's name everywhere else on the page, and the note's own first
  sentence, and a label is worth three lines only if it survives the render -- dropping
  "there" from the last sentence is what bought the room back at a ten-figure amount.
- **Phase 4 does the opposite, deliberately**, and uses `_fund_type_coefficient`.
  Bonds inside a Roth's target-date fund really are bonds occupying tax-free space,
  exactly as phase 1 counts them -- and a TDF account is pinned by its own budget row
  anyway, so counting them states the truth without giving the solver anything to act
  on.
- **Phase 3 penalizes only the sheltered *buy* side.** The taxable sale is the leg
  that realizes the loss, but it is also the leg phase 2 has already minimized and the
  one the portfolio usually has no choice about; penalizing it too would put phase 3
  in direct opposition to phase 1, which exists to sell exactly those taxable bonds.
- **A taxable holding of zero creates no wash-sale variable.** You cannot sell what
  you do not own. Without that check an empty taxable slot standing ready to receive a
  fund suppresses the very purchase phase 5 wants to make, over a sale that can never
  happen -- `test_an_empty_taxable_slot_does_not_suppress_a_sheltered_purchase` is the
  regression guard, and it caught this during implementation.

Phase 3 cannot condition on whether the taxable side is *actually* sold -- that is
decided by the same solve, and no linear objective can express it. The residue is a
mild preference against accumulating, in a shelter, a fund you already hold in
taxable. It costs nothing and usually points the same way as phases 4 and 5. What the
LP cannot avoid, `_wash_sale_notes` reports after the fact from the final trades.

A caution when testing placement: this LP is degenerate, so a scenario where the
preferred placement merely *ties* proves nothing -- the old solver often picked it
anyway. A test earns its keep only if it fails against the previous ranking; see
`test_international_is_moved_out_of_tax_advantaged_when_the_trades_are_free` and
`test_bonds_fill_tax_deferred_space_before_tax_free_space`. Phase 3 is especially
prone to this: the alternative-fund choice is usually volume-symmetric, so HiGHS
often picks the non-overlapping vertex on its own. Its tests are therefore built on
the deterministic paths -- an unavoidable overlap that must warn, and the zero-holding
exclusion -- rather than on a tie it happens to win.

`_OBJECTIVE_SLACK` exists because HiGHS is not bit-exact -- a hard `<=` against a raw
optimum can spuriously reject the next phase's true optimum. Don't set it to zero.
But note that every carried bound is also a *budget a later phase can spend*: giving
up that much of an earlier priority is permitted, and the volume-minimizing phase at
the bottom will take it. At the original cent, with five bounds stacked up, that
surfaced as "sell $5,999.99" where the answer is $6,000.00. It is now a tenth of a
cent -- clear of HiGHS's noise (verified from $100k to $8B) but below the cent grid
every displayed amount rounds to, so it cannot produce a visible artifact.

**A target the accounts cannot reach is approximated and disclosed, never refused.**
`_asset_class_reach` bounds each asset class by the smallest and largest coefficient
among an account's own slots, because the account must spend exactly its own total
across them -- so a single-fund account's floor and ceiling are the same number. Those
bounds are the allocation LP's bounds, and the objective aims at target from inside
them; where the target is out of reach the answer is the nearest point, which is what
`_resolve_allocation`'s second objective is for.

There is no infeasible case there to reject. Every slot's three coefficients sum to 1,
so each account's three smallest sum to at most 1 and its three largest to at least 1 --
which puts the sum of the floors at or below the portfolio and the sum of the ceilings
at or above it, and a box like that always meets the `sum = total_value` plane. (The
main LP can still be infeasible: `reach` bounds each class on its own, so it is sound
for rejecting the impossible, not for certifying the possible. That surfaces through
`_solve`'s message.)

**The band used to be those bounds as well, which made it the arbiter of feasibility.**
`_check_capacity_feasible` raised when the band and the reach did not overlap, so an
unreachable target was an error unless the band happened to be wide enough to cover the
gap -- widening a band silently converted a refusal into a plan, and a 0% bond target
against a target-date fund's bond sleeve could not be planned at all, which is how it
was found. Both it and `_band_note` are gone. The band is now the trigger and nothing
else.

`_capacity_notes` says what the reach costs, and its test is `_reachable_bounds`
having had to widen that class -- the band and what the accounts can hold do not overlap
at all, so the class is outside its band whatever else the portfolio does. Nothing
weaker will do: a class can also miss its band because the *other two* pinned the
dollars it needed, and which class gives way is then a property of the three together,
not a fact about that one, which on its own could have reached its target. The report
marks it outside its band and says no more, because the only available explanation
would be a false one.

Since an account holding individual funds declares all three, its coefficient for
every class runs 0 to 1 -- floor zero, ceiling its whole value -- so **a target-date
account is now the only thing that can pin one.** Both messages used to say so
outright, in a second indented paragraph ("an account holding a single fund has to put
its whole value into that fund, and a target-date fund's mix is fixed"); shortening
each note to one paragraph cut that, and each remedy now names one culprit and stops --
"hold target-date funds in a smaller percentage of the portfolio" against a floor,
"hold individual funds in a larger percentage" against a ceiling. Note the bound
itself is general -- `compute_trades` is public and tests call it with partial slot
sets -- so a message may name the likely cause but never asserts it, which is what makes
each of those a remedy to try rather than a diagnosis.

**The two directions are one sentence read twice.** "These accounts cannot hold less
than $X, or Y% of the portfolio. Raise the target, or ..." and "These accounts cannot
hold more than $X, or Y% of the portfolio. Lower the target, or ..." -- same verb, same
clause order, and only the bound, the target's direction and the kind of account that
causes it flip. The ceiling half used to open "No combination of the funds held reaches
more than", which is the same fact in a different sentence: two notes a reader meets in
one report, describing one obstacle from two sides, are read against each other, and a
difference in wording that carries no difference in meaning reads as a difference in
kind.

Ceilings are reported before floors: one account holding one fund breaches both at
once, and "nothing you hold can be bonds" points at the missing piece, while "you are
stuck holding this much U.S. stock" describes the same problem from the side the user
can do least about.

Trades below `MIN_TRADE_DOLLARS` are dropped as impractical.

### The rebalancing band (`allocation.effective_band_points`)

Two rules, and a class has to satisfy **both**, so the tighter of the two is what
binds -- **the 5/25 rule**. `band_pct` is the *absolute band*, in points of the whole
portfolio; `relative_band_pct` is the *relative band*, a percentage of the asset
class's target, so it scales with the target where the absolute one does not. Those
three names are what the prompts, the report, the saved keys and the README all say,
so a change to one of them is a change to all five places.

Neither alone works for all three classes. Five points is a quarter of a 20% bond
sleeve and far too loose for a 5% one -- five points below a 5% target is *zero bonds*,
which is how a portfolio holding barely a percent against a 5% target was reported as
in-band and left alone. Twenty-five percent of a 58.8% U.S. target is 14.7 points, far
too loose for the class that dominates the portfolio. Taking the lesser gives small targets the
relative rule and large ones the absolute cap. The two cross at a 20% target, where
both come to 5 points -- which is why the convention is usually stated as "5 points at
20% and above, 25% relative below": one rule, described twice.

`relative_band_pct` of `None` means the rule was never configured and only `band_pct`
applies. That is **distinct from `0`**, which like a `band_pct` of `0` tolerates no
drift at all. The distinction is what lets `compute_trades`'s `band_pct`-only default
keep meaning exactly what it did -- every solver test that says nothing about the band
still asserts exact-target behavior -- and it is the same "absent means never chosen"
that `rebalance_band_pct` uses in the config file.

**Both halves are required input, and neither offers a suggested answer.**
`prompt_rebalance_band` and `prompt_relative_rebalance_band` pass `default` straight
through, so it carries a *saved* answer and nothing else: a returning user presses
Enter to keep what they chose, and a first run has to type both. They used to fall
back to `DEFAULT_REBALANCE_BAND_PCT` / `DEFAULT_REBALANCE_RELATIVE_BAND_PCT`, which
meant the whole of step 2 could be walked past with two keystrokes. The band is the
one setting here that decides whether the program does anything at all, 5 and 25 are
a convention rather than a recommendation this program is in a position to make, and a
number the user never chose reads back in the report's "Rebalancing Bands" section as
their own policy. The constants stay in `config.py` as the documented convention --
what is gone is the program answering on the user's behalf. `TestRebalanceBandPrompts`
pins both halves of that: no suggested answer on a first run, a saved answer still
offered.

**The README says none of this, deliberately.** It claimed "both are asked outright
with no suggested answer", which is true of a first run and false of every run after
it, since a saved answer *is* offered back -- one of those halves is easy to state and
forget the other. It named the 5/25 rule in the same breath, which put a specific pair
of numbers in front of a reader as the convention while the program itself declines to
suggest them. Both went. Whether a prompt has a default is not what someone deciding
to install needs to know, and the two facts were only ever there together.

Note this makes `prompt_percent`'s no-default path load-bearing for the first time in
the flow: pressing Enter falls through to "Please enter a number." rather than
returning anything, which is what "required" means here.

**The relative half is one of the two questions in the flow that get explained before
they are asked** (the other is the three fund slots -- see `prompts.FUND_EXPLANATION`).
Everything else is asked bare and explained where its effect is visible, in the
report. That does not work here: "or by more than this percentage of its target"
reads as an alternative when it is a second, tighter limit, and the reason the rule
exists -- five points of drift is the whole of a 5% bond sleeve -- is invisible from the
prompt. So `prompts.BAND_EXPLANATION` states the policy above the pair, worded the way
one is written in an investment policy statement -- an asset class "drifts from its
target" by more than "the smaller of" two bands. That one sentence carries all the
semantics, which leaves each question below naming only its own unit (`pts` against
`%`) -- the part that was actually ambiguous. It stays one sentence: drafts that also
named the 5/25 rule, said what the relative band is for and noted that zero turns the
band off were all cut back to what a reader needs in order to answer the two questions.
The report's "Rebalancing Bands" section is where the band's effect is visible, and it
writes the resulting ranges out per class. The questions are "Absolute
band" and "Relative band": the industry's own names for the two halves, and the words
`rebalance_band_pct` and `rebalance_relative_band_pct` are already named after, so the
prompt, the saved key and the report all say one thing. `TestRebalanceBandPrompts`
holds this.

The vocabulary throughout is the Bogleheads wiki's and Larry Swedroe's, because that
is where a reader checking what to answer ends up: "rebalancing band", "asset class", an
asset class that "drifts from" its target, and the pair of numbers as **the 5/25
rule** -- absolute 5, relative 25. The rule is named in `config.py` and this file, and
nowhere the user can see it: not in the prompt, and no longer in the README. Where the two traditions disagree, precision
wins: Bogleheads writes the absolute half as "5%", which is 5 percentage *points*, so
the prompt's unit stays `pts`.

Because each class now has its own band, nothing user-facing may name a single number
for it. `report._describe_band` writes the three ranges out; `_describe_band_extent`
is the one place that decides between "the band of plus or minus X percentage points"
(absolute only) and "its rebalancing band" (both rules), and the comparison table's
footnote and the no-trades line both go through it.

**The no-trades line has to survive being read against the starred rows above it.**
Nothing to trade and a class still outside its band is neither "already matches the
target allocation" nor "every asset class is within its band" -- it is what the accounts
can hold that stopped it, so a third line says so. It reads `within_band` off
`summary.categories` rather than anything the solver reports: the summary describes the
current holdings, which with no trades are also the final ones, so it is right by
construction even where `_capacity_notes` has nothing it can truthfully say.

### VT allocation (`vt_allocation.py`)

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

### Output structure (`formatting.py`)

**A subheading's content starts on the line directly beneath its rule.** No
blank line between the two, in any section, ever -- the rule already separates
the heading from what follows, and a gap under one of six subheadings reads as a
different kind of division rather than the same one spaced differently. "Account
Holdings" and "Notes" both had one, because each emitted its separator at the
top of its loop and so put one before the first item as well as between them;
both now guard on the index. Blank lines still go *between* accounts and between
notes, which is the job that separator actually has.

Hierarchy uses two devices only: a rule under a heading, and indentation.
`=` banners the three steps *and the report they produce*, `-` underlines divisions
within either, and below that nesting is position alone -- an account is a plain label,
indented, with its contents one level deeper. Resist adding a third rule style; that
was tried and reverted. `format_result_header` is not a third style: same rule, same
width, just no step number.

**Every `-` subheading is Title Case; everything else is a sentence.** "Stock and
Bond Allocation", "Rebalancing Bands", "Account Holdings", "Current vs. Target
Allocation", "Orders to Place", "Notes", "Saved Accounts", "Add Accounts", "Update
an Answer", "Summary File", "Save Portfolio" --
the `=` banners above them are upper-cased by `format_section_header` anyway, and
everything below them is prose. A subheading names a thing rather than saying
something, which is what the casing marks. Short prepositions and conjunctions stay
lowercase ("and", "to", "vs."), the way a title is set anywhere else.

**The three actions after the report are `-` sections, not a fourth banner.** The
recompute gate, the summary file and the save each get one -- "Update Answer",
"Summary File", "Save Portfolio" -- so the tail of the run is shaped like the
questions above it. Two of them were flush until it was noticed that the first
thing under the disclaimer was a bare question, which is the exact problem "Save
Portfolio" had already been given a section to fix.

A `=` banner over the lot was considered and does not work, for two reasons worth
keeping. The gate is the loop's entry rather than a final action: answer yes and
the menu, a re-asked step-1 subheading and a second `=` REBALANCING SUMMARY banner
all arrive underneath it, which is a `=` inside a `=`. And past the loop there is
usually one thing left -- `--write-summary` is off by default and `--no-save`
removes the save -- so the banner would head a single yes/no on a normal run and
nothing at all on some, and a banner that sometimes has no section under it is
worse than none. The cost of the decision is that the report's banner now visibly
covers five report sections and three action sections; the summary file, which
holds only the report, is where the boundary is actually drawn.

The failed-solve path keeps its bare question deliberately. There is no report and
no disclaimer there, and "Update an answer and try again?" sits directly under the
one sentence explaining why it is being asked -- a rule between them would separate
the question from its reason.

**Two widths, both following the terminal.** `formatting.prose_width()` is
`min(terminal - 2, PROSE_MAX_WIDTH)`; `formatting.table_width()` is `terminal - 2` with
no cap. They diverge because they want opposite things: a paragraph gets *harder* to
read as it widens, while a table of dollar figures does not. Prose, notes and
the `=` banners all use prose width; tables are sized to their own contents within the
table budget.

This replaced a fixed 78, which was fine for prose but squeezed the tables -- the
comparison table silently passed 78 at a $5M portfolio, because seven-figure dollar
cells are four characters wider than five-figure ones, and no test covered it.
`terminal_width()` reads `$COLUMNS` first, which is what makes any of this testable;
`tests/conftest.py` pins it at 80 for the whole suite so wrapping assertions don't
depend on the window pytest happens to run in.

Never hand-break a paragraph: write it as one string and let `wrap` do it, so editing
the wording doesn't mean re-breaking the lines. `Prompter.say_wrapped` is the same
thing at the prompter's current depth. `wrap` keeps hyphens and long words intact,
because textwrap will otherwise split "tax-advantaged" across lines, and in a document
about tax treatment that reads as a different term.

**Only the per-account holdings table may exceed the width budget.** Everything else --
prose, notes, account headings, trade lines, the comparison table -- wraps or is
sized to fit, and `test_long_names_do_not_push_prose_or_headings_off_the_page` holds
the line. The exception is deliberate: a fund entered by its real name rather than its
ticker ("Vanguard Total Stock Market Index Fund Admiral Shares") cannot fit alongside
an amount in 78 columns, truncating it is how someone buys the wrong fund at the
broker, and wrapping it destroys the alignment the table exists for. It runs wide and
stays aligned. Nicknames are capped at input instead (`MAX_ACCOUNT_NAME_LENGTH`) --
those are labels the user invents, unlike a fund's real name, and they were what
pushed the headings off the page.

An account heading is always `nickname (type, treatment)` --
`Vanguard Roth IRA (Roth IRA, tax-free)`, `Vanguard Brokerage (Brokerage, taxable)` --
with the treatment *inside* the parentheses. Inside rather than after a dash because it
is shorter and safe at the nickname cap: the longest possible heading lands well inside
the page rather than wrapping and stranding a `--` at the end of a line. Uniform
because one line shaped like the next is what lets the eye compare them down the page.

There used to be a rule suppressing the treatment when the type already named it, for
the sake of the account type then called `Taxable Brokerage`. Since v4 renamed that to
plain `Brokerage`, **no account type names its own treatment**, and the branch was
removed rather than left unreachable. Reintroducing a type that does -- a
`Tax-free Savings Account`, say -- is what would bring the question back.

### Wording the output has to keep

These are compliance-driven, not stylistic, and an edit that reads better but
loses them is a regression:

- **The report always ends with the disclaimer.** It is the artifact that gets
  screenshotted and acted on days later; a disclaimer that lives only in the README
  does not travel with it. `report.DISCLAIMER` is the one copy -- `--help`'s epilog is
  that same object rather than a second wording of it, so the two cannot drift apart
  (`test_help_carries_the_report_s_own_disclaimer`). It is **two clauses**: not
  investment, tax or legal advice, and **not a recommendation to buy or sell** -- the
  Reg BI / FINRA 2111 term of art, and the other half of never using the word above.

  **It is two lines, and stays two lines.** A longer draft also disclaimed the advisory
  relationship, order placement and trademark use. All true, all cut: eight lines of
  legal prose at the foot of a page is something a reader learns to skip, which costs
  the disclosure the one thing it is there for. Those clauses are not restated elsewhere
  either: the README's Disclaimer section was cut back to the same two clauses plus a
  pointer to Limitations. Adding a clause here means finding one to cut.
  `TestRequiredWording` pins both the wording and the line count.

  **Non-affiliation is no longer stated anywhere the program prints.** `--version`
  carried it until it was cut back to `prog + version`; nothing replaced it. Worth
  knowing before the next edit here: this file's own history is the argument for
  brevity, not for the clause being unnecessary, and the fund and broker names still
  appear throughout the prompts and the README.
- **Nothing is called a "recommendation" and no order is phrased as an instruction.**
  "Recommendation" is a term of art under Reg BI and FINRA Rule 2111. Hence "Orders to
  Place" and "Review each order before placing it:" rather than "Recommended trades"
  and "Place the following orders:". The disclaimer's "not a recommendation to buy or
  sell any security" is the explicit denial that goes with the avoidance.

- **"Order" and "trade" are not synonyms -- use the industry split.** An *order* is the
  instruction you submit to a broker; a *trade* is the transaction that results, and
  the activity in general. So: "Orders to Place", "Review each order before placing
  it", "before placing these orders", "the above orders do not reach the target",
  "once these orders are filled" -- all instructions. And: "no trades needed", "the
  trades needed to rebalance", "taxable trade volume" -- all activity or outcome. The
  giveaway is the verb: you *place*, *submit* and *fill* an order; you *make* a trade
  and live with its result.

  Note that "not yet submitted" does **not** make something a third kind of thing.
  Everything under "Orders to Place" is an order that has not been placed, so a
  sub-minimum one that was dropped is simply an order missing from the list -- "One
  order smaller than $1.00 was left out", not "one move". A third noun for the same
  object is a vocabulary the reader has to learn for no gain. It does force "so the
  above orders do not reach the target exactly" at the end of that sentence: with a
  dropped order named in the same breath, "these orders" points at either set.

  Code identifiers stay on "trade" (`Trade`, `compute_trades`, `MIN_TRADE_DOLLARS`)
  because in portfolio-rebalancing systems the computed output is a *trade list* --
  "order" belongs to the execution layer this program never reaches. That is a
  deliberate split, not an oversight.
- **Tax statements are conditional.** The wash-sale note says a sale "may be" a wash
  sale -- never that it *is* one, or that a loss *is* lost. The tool cannot see cost
  basis, trade dates, or purchases made elsewhere in the 61-day window, so it flags
  the shape and stops short of the conclusion. `TestWashSaleAvoidance` pins the
  conditional.

  **It states the finding and nothing else.** Successive drafts have cut everything
  around it. First a suggestion to hold a different fund in the sheltered account
  (advice, which is the one thing this program does not give) and a note that matching
  by name misses two share classes of one index (a limitation of the check, which is
  the README's Limitations section's job). Then the statute itself -- section 1091's
  window and standard, and the IRS's position (Rev. Rul. 2008-5) on a replacement
  bought inside an IRA. That last was seven lines, the single largest block below the
  orders, and it was law rather than anything about this portfolio: a reader who wants
  to know whether their own replacement fund is far enough away is looking the rule up
  regardless, and a reader who wants to know whether to place the order was scrolling
  past it. Three lines rather than nine, all of them this portfolio's own numbers.
  The same test asserts the statute is *gone*, so it does not creep back a clause at a
  time.
- **A taxable sale is disclosed as a taxable event.** `report._taxable_sale_note`
  says it "may realize capital gains or losses" and, in its `detail`, that no cost
  basis is collected.
  Phase 2 minimizes taxable *volume*, which is not the same as pricing the sale, so the
  wording must neither skip the disclosure nor imply the solver costed it. Only the
  sale leg triggers it; a taxable buy realizes nothing.
- **The landing allocation is conditional on the orders filling.** "If these orders
  fill at the values entered here, the portfolio will hold ...", not "After these
  trades": an order fills at the market's price on the day, not at the figure typed
  into the prompts, so the number is arithmetic rather than a promise. It is a full
  sentence, naming each class in the words the rest of the report uses ("U.S. stocks",
  "international stocks", "bonds") rather than a slash-separated fragment.
- **The output does not address the reader's holdings in the second person.** Not
  "your portfolio", "your accounts", "your target" or "the funds you hold" -- "the
  portfolio", "these accounts", "the target", "the funds held". It reads as a statement about the
  portfolio in front of you rather than a claim about you, and it is one voice across
  the report, the prompts and the solver's notes, which were written at different
  times and had drifted apart. **One sentence is exempt, and it is a fixed formula**:
  `DISCLAIMER`'s "Consult a professional about your situation". Reworking it to dodge
  the pronoun is a worse trade than the pronoun. (The wash-sale note's "in any account
  you control" was the second, and went with the statute it belonged to.)
  The README follows the same rule where it describes what the program prints; its own
  documentation voice ("puts the CLI on your PATH") is unaffected.
- **The tax-treatment labels are not glossed.** "Tax-free" used to carry a line under
  the accounts saying it meant qualified withdrawals only. It is standard shorthand,
  the conditions on it are the reader's plan documents' job, and the report states what
  each account is and stops.
- **A prompt that classifies tax treatment says when the tax is paid, accurately.**
  The "Other" account's three choices are the only place the program explains the
  distinction, and they had said gains in a taxable account are taxed "every year".
  They are also printed unwrapped by `prompt_choice`, so each has to fit
  `prose_width()` -- `TestTaxTreatmentChoices` holds both lines.
- **No claim implies future performance.** Nothing may assert that stocks will
  out-grow bonds; where the asset-location preference is described at all -- the
  README's "Asset location" entry -- it is "a common convention", not a prediction.
  The onboarding flow used to say this itself, above the cash question, and no longer
  does: it explained a trade the user had not been shown yet, and the program prints
  no other unprompted commentary on its own reasoning.
  `test_the_asset_location_note_is_not_said_during_onboarding` holds the line.
- **The report says when it was made**, as its first line -- "Generated August
  29, 2026 at 9:03 PM EDT." That is the *document's* provenance and it leads;
  the figures carry their own further down, which is why the two are not
  together. It comes from `RebalanceInputs.generated_at` rather than from the
  clock inside `format_report`, so the same inputs render the same report and
  the summary file's name is stamped from the same instant the sentence names.
- **Every date and time the program prints or saves is the user's own local
  one.** `cli._now_local` is the only clock, and everything -- the line above,
  the summary file's name, the saved `values_as_of` -- reads from it. UTC is
  what the machine keeps, not what a person can act on: a stamp that has to be
  converted before it answers "was this before or after I moved that money" is
  a worse answer than no stamp. `values_as_of` was UTC's *date* until this
  rule existed, which put anyone west of Greenwich running an evening session
  a day into the future -- "Last saved August 30, 2026" for figures typed on
  the 29th, every evening, silently. `TestSavedDateIsTheUsersOwn` pins it by
  freezing `_now_local` at a New York evening whose UTC date is the next day.
- **Figures carry their provenance** -- "Values as entered, not live market prices.",
  plus "Last saved July 31, 2026." as its own sentence when they came from a config
  file. The numbers are the user's, and can be stale. The date is written out in full
  like every other date the program prints; see `formatting.format_date`.
- **Dropped sub-minimum moves are disclosed**, so trades that do not reach the target
  exactly are explained rather than looking like an arithmetic error. The count is
  spelled out through nine (`report._count`): the sentence opens on it, and "1 order
  smaller than $1.00 was left out" reads as a fragment rather than a sentence.
- **A target the funds cannot reach is disclosed, not silently approximated.** The plan
  goes as close as the accounts allow and `_capacity_notes` says which class, what it
  can reach, in dollars and as a share of the portfolio, and what the user could change.
  It states the reachable bound rather than where the plan happened to land, so the
  claim is true of the accounts and not merely of this solve -- which is also why it
  fires only where that bound is provably the obstacle; see the solver section.

**Indentation is carried by `Prompter.indented()` and `INDENT_UNIT`, never spelled
into a prompt string.** `_prompt_target_date_allocation` and `_prompt_new_holding` are
each called from two places at different depths, so a literal `"    "` that lines up
in one lands two levels off in the other -- which is exactly what happened, and what
let a `say_wrapped` conversion silently drop a line four columns out from its own
siblings. Every level steps by exactly one `INDENT_UNIT`.
`TestIndentation` pins the report's depths.

**A number carries the precision its neighbours need, and nothing more.** Two rules,
both in `formatting`, and the difference between them is whether the figure has a
column to line up with:

- **In prose, every value is written as short as it goes** -- `format_percent_prose`,
  which is `format_percent_at(v, percent_places([v]))`. "Derived from 80% stocks and
  20% bonds", "VT's 62% U.S. allocation". A sentence has nothing to align to, and
  "20.0%" in one is a precision the figure does not have. This *replaced* a rule
  fixing every percentage in the report at one decimal place; the argument for that
  one was that "20% bonds" two lines under "20.0%" reads as an inconsistency, and the
  answer is that the two are in different places doing different jobs.
- **In a table, every value of one unit shares one precision** -- `format_percents`,
  which is `percent_places` over the whole set and then each value at that. The
  comparison table's current and target shares are one unit and are read against each
  other across the row, so they share; its drift column is percentage *points* and
  gets its own. The three band ranges share all six of their edges. A column holding
  62.5 writes its 38 as "38.0", because the point of a column is to be read down the
  page.

`PERCENT_MAX_PLACES` is 1 and `round_percent` applies it before anything measures a
value, so a non-terminating division is measured on what will be printed rather than
on its 28 significant digits. It rounds **half-even**, which is the decimal context's
own default and therefore exactly what `f"{value:.1f}"` did here before any of this: a
band edge of 6.25% has always printed as 6.2%, and a rounding rule is not something to
change as a side effect of a formatting change.

`formatting.format_percent` is untouched and still **trims trailing zeros** for
prompts and echoed-back values, so a default reads the way someone would type it and
one prompt never offers `[80]` while the next offers `[62.0]`. `prompt_percent` is the
single door for asking one: it owns the 0-100 bounds, the `(%)` suffix, and the default
formatting.

**Dollars always carry cents, and a money column is aligned on them.** The comparison
table's dollars and the share in parentheses beside them are *two* columns, not one
cell: aligned as a single string, a five-figure amount next to a six-figure one lines
up on whatever trails it and the cents wander, which is what `$1,289.17 (1.2%)` under
`$40,187.16 (37.5%)` used to do. Each of the four is sized to its own contents -- one
shared width across both money columns costs a character the 78-column budget does not
have. `test_the_cents_line_up_in_every_money_column` holds it.

**Every date is written out in full, wherever it came from.** `formatting.format_date`
takes an ISO date, an ISO timestamp or the fact sheet's own long form and answers
"July 31, 2026" for all three; `vt_allocation._format_as_of` delegates to it rather
than keeping a second copy. `describe_as_of` is the parenthetical: "as of July 31,
2026" for a date, and the bare note for the several fields that carry one instead
("manually entered", "manually specified via --vt-us-pct") -- "as of manually entered"
is not a sentence, which is why the test is a parse rather than a format.

**A set of percentages that must sum to 100 is asked for one short, and whatever is
left over is stated and confirmed, in the same words the question that derived it
used.** `prompt_stock_bond_allocation` asks for the "Target stock allocation" and says
"That leaves a target bond allocation of 20%. Use this value?" -- one noun phrase
across both halves, so the derived share reads as the other side of the answer rather
than as a differently-named quantity. `_prompt_target_date_allocation` is the same
shape one level down: it asks for "U.S. stocks" and "International stocks" and
confirms "That leaves 1.7% bonds. Use this value?". Questions for every member of
the set outnumber the degrees of freedom, which invites an answer that cannot be
honored and turns a typo into a form the user has to re-fill. A denial restarts from
the *first* question, because the number they want to change is one they typed -- the
derived one is not theirs to edit -- and the only remaining way to be wrong is for the
entered values to exceed 100 outright, which the target-date prompt rejects in place.

**A question the answers so far have already settled is not asked.** 100% U.S. stocks
leaves nothing for either of the other two sleeves, so the international question is
skipped and both are stated together: "That leaves 0% international stocks and 0%
bonds. Use these values?". Asking for a number that can only be zero is a question
whose only wrong answer is one the prompt then has to reject.

Both confirmations end in **a statement and then a question the user acts on**, not a
statement and a bare "Correct?". Every other yes/no in the flow is verb-led -- "Use
these values?", "Save this portfolio for next time?" -- and the derived share is
arithmetic, which is correct by construction: what is actually being asked is whether
to proceed on the number the user typed above it. `_confirm_remainder` is the one
place that shape lives, which is also what keeps **the noun agreeing with how many
values are actually on screen**: "Use this value?" for one, "Use these values?" for
two. The same agreement governs the VT lookup, which shows a U.S. share and an
international one and therefore asks for both.

One consequence to know: entered target-date sleeves now sum to exactly 100, where a
fact sheet rounding each to a tenth often does not, so a fund printed 64.0 / 34.3 /
1.6 is confirmed back as 1.7% bonds. `TargetDateAllocation` keeps
`PERCENT_SUM_TOLERANCE` and `fraction_of` keeps normalizing -- a config written by an
older version or by hand can still hold a sum of 99.9 -- and the tenth of a point would
have been spread across the three sleeves by `fraction_of` anyway.

A share of the portfolio is `%`; a distance between two percentages is **percentage
points**, abbreviated `pts` only where the words will not fit: the comparison table's
`Drift (pts)` header, and the absolute band prompt's unit suffix.
`TestPercentFormatting` asserts the report's count is one; `prompt_percent`'s `unit`
argument is the prompt side, and defaults to `%` so every other question is unaffected.

**Dollar amounts are right-aligned in columns.** The comparison table and the
per-account holdings list both compute their column widths from their own contents.
The point of putting figures in rows is to compare them down the page, which ragged
`label: $amount` lines defeat. A declared position holding nothing renders as `--`
rather than `$0.00`: it is capacity the solver can use, not a holding, and `$0.00`
gives it a precision it does not have.

**The orders close with where they land** (`_describe_outcome`) -- the question the
rest of the report only answers by implication. It is computed from the holdings
rather than the class totals, so a trade in a target-date fund moves all three sleeves
by their own fractions, and it is stated conditionally ("If these orders fill at the
values entered here") for the reason in the wording section below. **It is indented to
the depth of the account blocks above it**, because it belongs to the orders: set
flush it read as the first of the notes below rather than as the answer to them.

**Everything after that is a `Note`, and they go under one `-` subheading.** The tail
of the report is where several unrelated findings pile up -- a taxable sale, a class the
accounts cannot reach, a wash-sale overlap, an order too small to place -- and it was
the one part of the page carrying no structure at all: a run of flush paragraphs of the
same width and weight, no heading, in an order a reader could not infer, each prefixed
`Warning:` whether or not it was one. A two-line finding and a seven-line statute
recital looked identical, and there was no signal for where to stop reading.

`models.Note` is `label`, `summary` and an optional `detail`, and `report._describe_notes`
is the one place they land on the page -- the label leads the summary, so three words say
whether the paragraph is the reader's, and a `detail` sits one `INDENT_UNIT` in, where it
reads as optional.

**No note currently uses `detail`, and each is three lines or fewer.** Three did, and
they were cut to fit in one paragraph: the taxable sale's semicolon became a period, the
stranded-bonds note dropped "that can only be held whole", and the capacity note lost
both the target's own dollar figure (the comparison table two sections up prints it for
every class, in dollars and as a share) and the sentence explaining *why* the accounts
are stuck. Three lines is measured at the worst case, not the typical one -- the longest
label ("International stock target out of reach") against a ten-figure amount -- because
that is what decides whether a note ever spills to four. `_describe_notes` still renders
`detail`, and the split is still worth knowing if one earns its way back: **the summary
reports and the detail explains.**

**No colons or semicolons in a note.** Every clause is its own sentence or joins with a
comma -- including the wash-sale note's condition, which is "If any of those shares are
sold at a loss, this may be a wash sale": the comma is what keeps the conditional from
reading as one run-on clause, and "sold at a loss" rather than "at a loss" is what ties
the condition to the sale the sentence above it just described. `TestNoteWording` holds
this, the line count and the absence of `detail`.

What the capacity note gave up is worth knowing before shortening it further. Its remedy
names one culprit for its own direction and stops, so a reader who does not already know
that a target-date fund's mix cannot be split will not learn it from the note. That was
the deliberate trade for one paragraph.

The `Warning:` prefix is gone with them: several of these are not warnings -- a taxable
sale is a disclosure, a dropped order a footnote -- and under a heading the prefix only
repeated what the heading said. Which is also why the field is `RebalanceResult.notes`
rather than `warnings`: the printed word and the code's name for it agree, as they do
everywhere else here.

**Report-side and solver-side notes interleave in `format_report`**, and the order is
deliberate: the taxable sale leads, because it is the consequence of placing these
orders at all; `result.notes` follows in the solver's own order (capacity, then bonds
stranded in taxable, then international bought in a shelter, then wash sales); and the
dropped-order footnote trails, because
it is about the completeness of the list rather than about the portfolio. The
dropped-order note fires only when there *are* orders -- "the above orders" has nothing
to point at otherwise.

The report restates every answer it was given -- target allocation and where it came
from, the band, the accounts and their holdings -- before the current-vs-target summary
and the trades. Read on its own with no scrollback it should still say what was asked
for and what to do. `RebalanceInputs` carries that set, so recapping one more answer
does not mean growing `format_report`'s signature again.

`Prompter.indented()` carries depth for interactive output, so indentation is a
property of where you are in the flow rather than something spelled into each string.
`_at_depth` intentionally leaves leading blank lines flush -- several messages open
with `\n` as a separator, and padding it would emit trailing whitespace.

**`report.py` must not import `prompts.py`.** Shared presentation constants
(`INDENT_UNIT`) live in `formatting.py`, which both import.

### The summary file (`--write-summary`)

Off unless asked. The program already asks before writing the portfolio file,
and a summary carries the user's whole net worth broken out by account, so
writing one unprompted into a dotdir they never browse is not this program's
call to make. `--write-summary PATH` writes there; the bare flag writes
`rebalancing-summary-<stamp>-utc.txt` beside the portfolio file.

**A path the user named is an instruction and is overwritten. A name this
program generated is a promise and is never overwritten** -- `_write_summary`
opens it exclusively and falls to a numbered sibling, which takes two runs
inside one minute but is the only thing that makes "no collisions" true rather
than merely unlikely.

**The stamp is one decision spelled twice.** `format_generated_at` is the
sentence at the head of the report ("August 29, 2026 at 9:03 PM EDT") and
`format_generated_at_for_filename` is the same instant as a file name can carry
it ("2026-08-29-2103-edt"). Same clock, same precision and same zone by
construction -- `_zone_labels` returns both spellings at once, because the only
way to be sure two renderings agree is for one function to decide both -- so a
file found on disk can be matched to its own first line. The file name is not
the sentence with its spaces removed: a name has to sort, survive a shell and
be legal on Windows, which the comma, the spaces and the colon each break.
Minutes because a plan is re-run within the day constantly, and the collision
suffix covers the rest.

**The zone is printed as an abbreviation where one exists and as a numeric
offset otherwise**, and the test is a *shape* -- `^[A-Za-z]{2,5}$` against
`tzname()` -- rather than a list of known zones, because three different
problems arrive through that one field. A zone with no abbreviation answers
"+0545" (Kathmandu, Eucla, Marquesas), which is not a word and must not be
printed as one. Windows answers a full phrase, "Eastern Daylight Time", and
answers it *localized*, so a non-English machine would otherwise put spaces and
non-ASCII into a file name. And an abbreviation is not merely shorter than an
offset: it is what tells the two 1:30 AMs of a fall-back apart, which a bare
local time cannot. `datetime.now(tz=timezone.utc).astimezone()` is how the zone
is found -- no dependency, and converting *from* an aware UTC instant is what
keeps the fall-back hour unambiguous where a naive `datetime.now()` would not.

Local time costs the chronological sort across a fall-back hour and across a
change of zone, and both are real. Neither can lose a file: a generated name is
opened exclusively and falls to a numbered sibling. Every test builds its own
zone with `zoneinfo` and passes it in, so nothing depends on the machine the
suite runs on -- `generated_at` is injected for exactly that reason, which
leaves `_now_local` as the single line the suite cannot cover and does not
need to.

**The file is rendered again at `SUMMARY_FILE_WIDTH`, not captured from the
screen.** Width is read globally by `prose_width`/`table_width` on the way down
through every renderer, so `formatting.fixed_width` pins it for the render
rather than threading a width through a dozen signatures. A file is read
somewhere other than the terminal that made it, so the same portfolio must not
land at 78 columns from one machine and 198 from another;
`test_the_layout_does_not_follow_the_terminal` writes both and diffs them. It
is written *after* the report is on screen, so an unwritable path costs a
message and not the plan.

**`--no-save` and `--write-summary` govern different files**, which is most of
why the new flag is not called `--save-summary`: beside an existing `--no-save`
that reads as its opposite number, and it is not. `--no-save`'s help now names
the portfolio file outright for the same reason, and the README says it in a
sentence, since a help string is not where someone resolves a confusion they
have not had yet.

### Persistence

`~/.three_fund_rebalance/config.json`, versioned by `SCHEMA_VERSION`, written
atomically (temp file + `os.replace`). Saved values are re-offered as *editable
defaults*, never silently trusted.

**The saved accounts are listed before they are asked about, and the instruction is
said once.** Step 3 lists them vertically under "Saved Accounts" -- one name per line,
because those names are the headings the questions below arrive in and a list read
down the page is what lets someone match one to the next -- then says how to answer
them ("For each, press Enter to use its saved value, or type a new value.") **above
the list rather than at the head of each account**, where it said nothing the previous
account had not already said. Each account then opens with "Keep this account?", which
is the one way the flow drops a saved account; answering no says `Removed '<name>'.`
and moves on. `TestSavedAccountsLine` pins the list and the single instruction.

**Every way a config file can fail to load raises `PersistenceError`** -- that is
what `cli.run()` catches to warn and continue blank instead of crashing, so any
other exception escaping the parse takes the whole run down over a file the user
can hand-edit. Valid JSON of the wrong shape counts: `"accounts": 7`, a holding
that isn't an object, a name that's a list. The inner parsers name what's wrong
where they can, and `config_from_dict` wraps the lot in a catch-all that converts
anything unanticipated (re-raising `PersistenceError` untouched so specific
messages survive). `tests/test_persistence.py::MALFORMED` is the table to extend
when a new shape shows up.

A config saved before accounts became one-kind-or-the-other can hold a mix, and no
longer loads; `Account`'s message names the account, and `cli.run()` warns and starts
blank as it does for any `PersistenceError`. That is deliberate -- splitting such an
account automatically would invent an account boundary that is a hard constraint on
the solver.

The file is at v4, and upgrades run **one hop at a time** -- `config_from_dict` chains
`v1 → _upgrade_v1 → v2 → _upgrade_v2 → v3 → _upgrade_v3 → v4`, so a v1 file walks the
same path a v3 file does. Each upgrade translates without validating: anything still wrong surfaces from
the normal parse, so a corrupt old file reports what a corrupt current file would.
Each copies at every level, because a failed load must not leave the caller's parsed
JSON half-renamed. Any further rename of a persisted name needs another hop, not an
in-place edit of an existing one -- and `_upgrade_v1` must keep returning `2`, not
`SCHEMA_VERSION`, or it will skip every hop added after it.

- **v1 → v2** spelled the fund types after the academic asset classes
  (`domestic_equity`, `tdf`, `balance`, `balances_as_of`); v2 uses the same words the
  CLI prints (`us_stock`, `target_date`, `value`, `values_as_of`).
- **v2 → v3** splits the single `tax_advantaged` treatment into `tax_deferred` and
  `tax_free`, re-inferred from the account's own persisted `account_type` via
  `ACCOUNT_TYPE_TAX_TREATMENT`. An unrecognized type -- including `"Other"`, whose v2
  answer was a yes/no that never recorded the difference -- becomes `tax_deferred`:
  bonds fill that space first, so guessing this way costs nothing if it is wrong.

  Note `_upgrade_v2` looks types up in the *current* `ACCOUNT_TYPE_TAX_TREATMENT`, which
  no longer holds v2's spellings. That is safe only because the lookup runs solely for
  accounts marked `tax_advantaged`, and the one type v4 renamed is taxable. A rename
  that touches a shelter will need `_upgrade_v2` to carry its own frozen v2-era map.
  `rebalance_band_pct` is deliberately left *absent* rather than defaulted, because
  absent means "never chosen" and the step 2 prompt offers the default; writing one in
  would make a guess look like the user's own saved answer.

- **v3 → v4** renames the `Taxable Brokerage` account type to `Brokerage`. Every other
  entry on the list is the account's actual name -- Roth IRA, 403(b), HSA -- while
  "Taxable" is a descriptor, and Title-Casing it put the one word the report otherwise
  always writes lowercase (beside "tax-free" and "tax-deferred") into a proper noun. An
  account type the map does not know, `"Other"` included, is left exactly as it is.

`rebalance_relative_band_pct` was added later **without a hop**, and deliberately: a
new optional key translates nothing, and its absence already means "never chosen"
exactly as an absent `rebalance_band_pct` does. A hop is for a name or a meaning that
changed. Note that `_upgrade_v2` now writes the literal `3` rather than
`SCHEMA_VERSION` -- same trap as `_upgrade_v1`, harmless only until the next hop
exists.

## The README

It answers "what will this print, what does it optimize for, and what will it not
do" -- for someone deciding whether to install it and whether to trust the plan. The
middle question earns the solver a place there, but only at the altitude of *what is
being optimized and in what order*: the ranking as six one-line clauses, the two
stages, and the fact that ranks are lexicographic rather than weighted. **How** any of
it is computed still lives in this file alone -- the variable layout, the carried
bounds, `_OBJECTIVE_SLACK`, the implied third equality, which phase reads
`_fund_type_coefficient` and which reads `slot.fund_type`. A README that starts
explaining a phase rather than naming it is the failure mode to watch for.

Sections, in order: the one-paragraph blurb, Disclaimer, Example, Install, Running,
How it works, Limitations, Development, License.

**The Example is real output, pasted verbatim.** It is the first thing a reader sees
and the reason the README is structured around it, so it may never be hand-edited or
hand-idealized -- re-generate it and paste the result. Any change to `report.py` or
`formatting.py` wording means re-generating it. To do that, drive `run()` with a
scripted prompter as the tests do (never by piping stdin -- see "Running the CLI
without side effects"), under `COLUMNS=80`, which is what `tests/conftest.py` pins the
suite to and therefore the width every wrapping assertion in the repo assumes. The
scenario is 80/20, a 5/25 band, and three accounts, each declaring all three funds:
a Brokerage holding $60k VTI and $30k VXUS, a Roth IRA holding $20k VTI, and a
Traditional 401(k) holding $30k VTI and $10k BND. The two empty Roth slots are the
point of the example -- they are what lets the whole bond target land in the shelters,
so the taxable account is left alone and the report carries no taxable-sale
disclosure.

**One line of the Example cannot come from such a run.** Passing `--vt-us-pct` to skip
the network stamps the provenance line "manually specified via --vt-us-pct"
(`cli.py`), where the README shows the fetched form -- `formatting.describe_as_of` on a
real date, e.g. "(as of June 30, 2026)". The README deliberately shows the fetch path,
because that is what a reader running the CLI normally will see. Substitute that one
line by hand.

**Two things the CLI really prints are cut from the Example**, and a regeneration has
to cut them again -- they are the first and last things a naive paste puts back:

- **The "Generated ..." line**, which opens the report. It is a wall clock in whatever
  zone and minute the regeneration happened to run in, so pasting it dates the *README*
  rather than the example, and every later regeneration shows up as a diff in a line
  that carries no information about the program. There is nothing to learn from it that
  the surrounding text does not already say.
- **The closing disclaimer.** It is two lines the README has already given in full, in
  its own `## Disclaimer` section directly above the Example. Repeating it inside a
  fenced block a screen later is the fourth-hand restatement the disclaimer's own entry
  under "Wording the output has to keep" argues against -- and cutting it here changes
  nothing about the rule that the *program* always ends on it, which is where it does
  the work.

Everything between those two is exactly as printed. Note this makes "real output,
pasted verbatim" mean *a contiguous run of it*: the trim is at the ends only, and
nothing inside may be touched or idealized.

**How it works is a list of bolded lead-ins, each followed by at most a short
paragraph** -- two to four lines. It is a summary, not a specification. An entry that
needs more room is either two entries (the band's definition and the band's trigger
semantics are split for exactly this reason) or a Limitations bullet. Growing one past
a short paragraph is the thing that keeps happening; splitting it is the fix.

**The ranked list is the one exception**, because a ranking is the one thing a
paragraph cannot carry: six preferences in prose reads as six things the solver
balances, which is precisely what lexicographic ordering is not. It is six numbered
items of one line each, and each has to survive being read against `_location_objectives`
-- the ordering there *is* the list here. It sits *inside* the "Preferences are ranked,
not weighted" entry, between that paragraph and the one sentence on what can open a
taxable trade, rather than under a bolded lead-in of its own. Splitting it out was
tried: it produced a lead-in that was not a sentence, and stranded the taxable-trade
rule outside the list it qualifies. Two clauses in it are load-bearing beyond
their length. Item 1's "since their interest is taxed yearly as ordinary income" is the
only justification given for the whole shelter preference. Item 4's "by common
convention" is required: saying tax-free space is for stocks *because* stocks grow more
is a claim about future performance, which nothing here may make -- see "No claim implies
future performance".

**But compressing one until it says something false is the worse failure**, and it has
happened. An entry read "Only bond placement opens a taxable trade. Trades inside
sheltered accounts cost nothing" -- two false claims in one lead-in. Reaching the
resolved allocation opens taxable trades too (those are hard equalities; phase 1 is
merely the highest *preference* that can open one), and a sheltered trade realizes no
capital gain but still pays spreads and fees, which Limitations already discloses. A
lead-in that ranks or excludes something has to survive being read against the phase
list; when it cannot be made both short and true, it is a Limitations bullet.

**Limitations is where caveats go**, as bullets with bolded lead-ins, which is what
lets How it works stay short. A newly discovered thing the tool cannot see is a bullet
there, not a qualification bolted onto a paragraph above.

**Both sections run roughly in the order a run meets them** -- for Limitations, the
lookup, then the step 3 questions, then the report top to bottom, then what happens at
the broker; for How it works, step 1, step 2, step 3, then the solve. Nothing says so on
either page; a lead-in announcing the order was written and cut, because an order either
reads naturally or does not, and one that needs explaining is the wrong order. It is
only roughly true: the muni bullet sits with the orders, since that is where a muni
holder notices, rather than with the question where the ticker was typed.

How it works did not always follow it, and the failure was invisible until the two
sections were read against each other: the three entries about what you are *asked*
sat last, with the VT split -- the first line of the Example directly above -- dead last
of all. The objection to fixing it is real and was weighed. Run order opens the section
on where a number comes from rather than on what the tool does with it, which buries the
lede by one entry. It wins anyway, because the Example ends on that same VT provenance
line, so the two read continuously; and because the section now closes on the ranking
instead of trailing off into fund-entry rules, which is the stronger place for it.

**A mechanism goes above and its caveat goes below, and neither restates the other.**
Three pairs are split that way on purpose -- "Name a fund you don't own yet" against the
restricted-lineup bullet, ranking 5 against "a rule of thumb", ranking 2 against "No
cost basis". The failure mode is the caveat re-explaining the mechanism to set up its
own point: "No cost basis" used to open by re-describing the taxable-volume proxy, which
the ranking now states, and "It knows nothing about" ended on a 401(k)'s fixed fund menu,
which is the whole subject of a bullet two above it.

**The Disclaimer section is `report.DISCLAIMER`'s two clauses plus a pointer to
Limitations, and nothing else.** The clauses cut from the report -- advisory
relationship, order placement, trademark use -- are not restated here either; see the
disclaimer entry under "Wording the output has to keep" for why.

**Every name the README uses for a user-visible concept is the program's own name for
it.** The two bands, the order/trade split, and the ban on "recommendation" all apply
here exactly as they do to printed output -- the README is one of the places the band
names have to agree, and a rename is a change to all of them at once. The 5/25 rule is
the exception in the other direction: it is a name for something the program never
shows, so the README does not use it either.

Mechanically: prose wraps at 78 columns, hard; `--` for a dash, never an em dash, so
the source matches what the CLI prints; asterisk emphasis for the band names on first
use. Only an unbreakable line may run past 78: a row of the options table under
Running, which cannot be wrapped without breaking the table. Nothing inside a fence
does any more -- the install commands are all short since they name a PyPI package
rather than a git URL.

## Releasing

The package is on PyPI as `three-fund-rebalance`, and the README's install
instructions name it rather than a git URL. A release is a tag:

```bash
pytest -m network   # the live sources, which CI never checks -- see below
# bump __version__ in three_fund_rebalance/__init__.py, commit it, then
git tag v0.5.0 && git push origin v0.5.0
```

**`pytest -m network` is a release step, and this is the moment it exists for.**
The default suite mocks every network call, so a rotted VT source is invisible to
CI and to every contributor until a user runs the CLI and quietly gets the
fallback. Cutting a release is the one scheduled moment when someone is paying
attention, so it is where the check belongs. A failure here is not automatically
a bug in this repo -- Vanguard may just be down -- but it must be understood
before tagging, not after: the alternative is shipping a version whose primary
source does not exist, which is exactly what 0.1 through 0.5 did.

It is deliberately **not** a step in `publish.yml`. Two reasons, and the second
is the one that decides it. A third-party outage would block a release for a
reason that has nothing to do with the release. And the tests would run from a
GitHub runner's datacenter IP, which is precisely the kind of client the
interactive site's bot protection treats differently from a laptop -- so a
failure there would be ambiguous in the one place ambiguity is most expensive.
Run it locally, where a failure means what it says.

`.github/workflows/publish.yml` fires on `v*`, builds an sdist and a wheel, and
uploads them through **PyPI Trusted Publishing** -- PyPI mints a short-lived token
from the workflow's OIDC identity, so there is no API token in the repo or in
GitHub secrets. What makes that work lives outside the repo and is invisible from
inside it: a publisher registered on PyPI for owner `melvinrajendran`, repository
`three-fund-rebalance`, workflow `publish.yml`, environment `pypi`, plus a GitHub
environment of that same name. All four have to match or the upload is rejected.

The workflow's first step asserts the tag equals `__version__`, because the version
otherwise lives in exactly one place (`__init__.py`, read by `pyproject.toml`'s
`dynamic = ["version"]`) and the tag is a second place to get it wrong -- a mismatch
would publish a version nobody can `git checkout`.

**A version, once uploaded, can never be replaced or reused**, even after a delete.
A botched release is fixed by bumping to the next version and tagging again, never
by re-cutting the same one.

## Testing conventions

Test files mirror modules 1:1; tests are grouped in `Test*` classes by the function
under test. Names are full sentences describing the behavior.

`Prompter` takes injected `input_func`/`print_func`, so the entire interactive flow is
driven by a scripted list of canned answers -- **no monkeypatching of builtins**.
`tests/test_cli.py::ScriptedPrompter` and `new_account_responses()` are the helpers;
reuse them rather than writing new stdin plumbing. Adding a question to the flow means
threading one more answer into every scripted list. `NO_REVISION` is the one
every flow that reaches a report now has to answer ("Update an answer and
recompute?"), named rather than left as a bare `"n"` among the account answers
because it is the only one that is not about an account. Note the scripted
prompter discards question *text* -- it goes to `input_func` -- so a test can
never assert on a prompt's wording, only on what `say` printed and on
`all_consumed()`, which is what actually pins "one answer was re-asked and not
the whole flow". The band's two answers sit
between the VT allocation and the first "Add an account?", and existing flows pass
`"0"` for both so they keep testing exact-target behavior.

Two shapes to know when writing one. A stock/bond target is `"80", "y"` -- the stock
share and then the confirmation of the derived bond share -- and a target-date
allocation is `"60", "20", "y"` for the same reason. (`"100", "y"` is the third: a
first answer of 100 settles both remaining sleeves, so the second question is never
asked.) An account holding individual funds is a name and a value per asset class with
no yes/no between them, in the order `_INDIVIDUAL_SLOT_PROMPTS` lists; the update path
asks the same questions with the saved ticker and value as defaults, so `""` twice
keeps a holding exactly as it was -- behind a leading `"y"` for "Keep this account?",
which every saved account starts with.

`compute_trades`'s `band_pct` defaults to `Decimal(0)`, which is the exact target and
therefore the pre-band behavior -- solver tests that aren't about the band say nothing
about it and keep asserting the same numbers. `relative_band_pct` defaults to `None`
for the same reason: `0` there would collapse every one of those tests onto the exact
target by a different route, and silently.

Every network call is monkeypatched, including failure paths. The suite must stay
runnable offline -- CI depends on it.

`tests/test_network_sources.py` is the one deliberate exception, and it proves the rule
rather than bending it: mocks cannot tell you a URL has stopped being a URL, which is how
the VT endpoint stayed dead through several releases with every test green. Those tests
carry `pytest.mark.network`, `addopts` in `pyproject.toml` deselects them, and CI runs
bare `pytest` so it never sees them. Keep it that way -- a live source in the default run
would fail on a plane and flake in CI. What belongs there is only what a mock cannot
answer: that each source still responds, that the live payload still has the shape the
saved fixtures are written against, and that the two sources still agree to within a few
points. Assertions are deliberately loose, because a failure should mean rot rather than
a market that moved.

## Gotchas

`pyproject.toml` pins the ruff rule set explicitly: `[tool.ruff.lint] select = ["E",
"F", "I", "UP", "B", "SIM"]`. It used to select nothing, which left the active set as
whatever the resolved ruff version enabled by default -- so isort (`I001`) and
flake8-simplify (`SIM117`) were being enforced without anyone choosing them, and a
`ruff>=0.6` floor meant an upgrade could change what CI accepts in either direction.
Naming them makes the lint reproducible.

`E` is the half that matters day to day: `E501` is *not* in ruff's default set, so
`line-length = 100` was advisory and about thirty lines had quietly passed it. It is
enforced now. `B905` is the other one worth knowing -- every `zip()` over two lists
that are the same length by construction says `strict=True`, so a future change that
breaks that assumption raises instead of silently truncating.

Version lives in `three_fund_rebalance/__init__.py`; `pyproject.toml` derives it via
`dynamic = ["version"]`. Bump it in one place.

`license-files` in `pyproject.toml` is PEP 639 and requires `setuptools>=77`, which is
why the build-system floor is set there.
