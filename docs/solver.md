# The solver (`rebalance.py`)

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

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
