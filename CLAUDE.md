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
pytest --cov=three_fund_rebalance --cov-report=term-missing

ruff check three_fund_rebalance tests    # lint (CI runs exactly this)
ruff check --fix <path>                  # autofix
```

Re-run `pip install -e ".[dev]"` only when packaging metadata changes (a new/bumped
dependency, a renamed console script, a new top-level package) — new modules inside
`three_fund_rebalance/` take effect immediately under the editable install.

### Running the CLI without side effects

The interactive flow reads and writes `~/.three_fund_rebalance/config.json` and hits
the network. To exercise it safely:

```bash
python -m three_fund_rebalance.cli --offline --vt-us-pct 62 --fresh --no-save --config /tmp/scratch.json
```

Always invoke it as a module, never through the `three-fund-rebalance` console
script: that runs the working copy regardless of what else is on PATH, so a pipx
or uv install of the same name can never be what you actually tested.

To see rendered output non-interactively, drive `run()` with a scripted prompter
rather than piping stdin — see "Testing conventions" below. Piping to stdin is
brittle because the prompt sequence branches on answers.

## Architecture

One linear pipeline, orchestrated end-to-end by `cli.run()`:

```
load_config → prompt_stock_bond_allocation → resolve_vt_allocation → compute_target_allocation
            → prompt_rebalance_band → prompt_accounts → compute_trades
            → format_report → save_config
```

Modules map to stages: `persistence` ↔ config file, `prompts` ↔ all input,
`vt_allocation` ↔ the one network call, `allocation` ↔ percentages→dollars,
`rebalance` ↔ the solver, `report`/`formatting` ↔ output. `config.py` holds
constants only; `models.py` holds the dataclasses everything else passes around.

The user walks **three** numbered steps — target allocation, rebalancing band,
account holdings. The report is not a fourth: it is what those produce, so it gets
`format_result_header` (same `=` rule, no "STEP x OF y") rather than a step banner.
`cli._INPUT_STEPS` is the count, in one place.

### Invariants that span files

**Money is `Decimal`, never `float`.** The sole exception is inside the LP, which
necessarily works in floats; `rebalance._to_decimal` and `models.to_cents` convert
back at the boundary. Introducing a float into a dollar amount elsewhere is a bug.

**A rebalance never moves money between accounts.** Each account's total value is an
equality constraint. Trades only reallocate *within* an account, including investing
that account's available cash.

**There are two shelters, not one.** `TaxTreatment` is three-valued: `TAXABLE`,
`TAX_DEFERRED` (traditional 401(k)/IRA, 403(b), 457(b), SEP, SIMPLE) and `TAX_FREE`
(Roth IRA, Roth 401(k), HSA). Both shelters are exempt today; they differ in what a
dollar of growth inside them is worth, which is what decides that bonds belong in
tax-deferred space and stocks in tax-free. Anything not `TAXABLE` is a shelter —
`Account.is_tax_advantaged()` — and every taxable-vs-sheltered test in the solver is
written against `TAXABLE` so that adding a third shelter kind would not silently
change phases 1, 2 or 5.

**What to hold is decided before where to hold it, and never by it.**
`_resolve_allocation` settles the three asset-class totals first, honoring the
rebalancing band; the solver then hits those as hard equalities. Every location
objective is phrased as "minimize this asset class in that kind of account", which
means *relocate* only while the class total is fixed — see the solver section, where
letting the band reach those phases turned out to be a real bug.

**`FundType.CASH` has an implicit target of zero** — cash is always fully invested.
It is excluded from the tradeable slots and from `_TARGET_FUND_TYPES`.

**A target-date fund is one position holding a fixed internal ratio,** not three
positions. `_fund_type_coefficient` is what lets a single slot contribute
fractionally to all three targets.

**An account holds a target-date fund *or* individual funds, never both** (cash may
sit alongside either). `Account.__post_init__` enforces it, `prompts` asks which kind
up front instead of offering a fourth yes/no, and `INDIVIDUAL_FUND_TYPES` is the set
that clashes with `TARGET_DATE`.

The consequence worth holding onto: **a target-date account has exactly one slot, so
the per-account budget equality pins it outright.** No objective can reach inside it.
That is what stops the solver from liquidating a taxable target-date fund to relocate
the bond sleeve within it — which it used to do even for a portfolio already sitting
on its target. It also means such an account sets a *floor* under every asset class,
not just a ceiling, which is why `_check_capacity_feasible` checks both.

### The solver (`rebalance.py`)

Two stages. **`_resolve_allocation` decides what each asset class should be worth**,
then six phases decide where to hold it.

**The band is a trigger, not a destination.** The first stage opens with a gate: with
nothing uninvested it is plain `Decimal` and no solve at all — every class inside its
band returns `dict(current)` outright, "leave it alone" answered with the numbers the
portfolio already holds rather than with a solver's slack spent drifting a fraction of
a cent. Anything that trips the gate sends the whole portfolio back to target, all
three classes, not just the one that breached.

Stopping at the nearest band edge instead is what this did once, and it was worse on
two counts. It leaves the portfolio *on* the boundary, so the next small drift trips
the band again — rebalancing to the centre buys a full band's worth of quiet. And it
is under-determined: a class one point out of band can be brought back by selling
either of the other two at identical cost, so which one got sold came down to
whichever vertex HiGHS happened to return.

**Cash is handled first, and the band is then asked about what it leaves behind** —
not about the portfolio still holding it. That is `_place_cash`: a separate tiny LP
constrained to `p >= current`, which is what "spend the cash and sell nothing" means,
since only a sale can shrink a class total. It answers with the resulting totals when
the cash alone leaves every class inside its band, and `None` when it does not, which
is `_resolve_allocation`'s signal to rebalance properly.

It is lexicographic too, and the order is the other way round from its caller's: (1)
minimize how far outside its band each class is left; (2) among the ties, sit as close
to target as possible. The band comes first because it is what the answer turns on —
with two classes below target the cash could go to either and be equally close to
target overall, while only one of those choices might get the laggard back inside its
band.

**Testing the cash itself instead is a bug that shipped and was caught in real use.**
Treating any uninvested balance as grounds to reopen the question meant 24 cents swept
up from a dividend rebalanced a portfolio that was comfortably inside its band on all
three classes — $4,053 of trades, taxable sales included, over a quarter.
`test_cash_alone_does_not_trigger_a_rebalance` is the guard, and it is written on the
deterministic path: the cash reaches the laggard through a free swap in a shelter, so
the taxable account only ever buys.

Past the gate it is a tiny LP over three variables, lexicographic: (1) sit as close to
target as the accounts allow; (2) among the ties, move as little as possible from where
the portfolio already sits. (2) runs only when (1) comes back nonzero, i.e. only when
the exact target is unreachable — an account holding a single fund pins that fund's
share of the portfolio, and the closest reachable points are then a whole face rather
than a vertex. With U.S. stock pinned at 60% against a 50/25/25 target, every split of
the remaining 40% is exactly as far from target as every other;
`test_an_unreachable_target_settles_nearest_to_where_the_portfolio_sits` fails without
(2), which is what earns it its keep.

**Skipping that stage and handing the band to the six phases below is a bug that was
shipped once and caught in real use.** Given a portfolio inside its band but with
international parked in a Roth and a taxable account too full to take any, phase 5
could not relocate — so it satisfied itself by *selling* international and buying U.S.
stock up to the band ceiling, while phase 4 liquidated a bond fund the portfolio was
already 3.8 points underweight. Both objectives are stated as "minimize this asset
class in that kind of account", which only means "relocate it" while the class total
is fixed. `TestAllocationIsSettledBeforeLocation` pins the whole shape.

The six location phases then run over one shared variable layout, against the resolved
totals as hard equalities — each phase's optimum carried forward as a `<=` bound so
later phases refine but never undo earlier ones:

1. Minimize bonds left in taxable accounts (fill sheltered room first).
2. Minimize trade volume *within taxable accounts* — the proxy for avoiding capital
   gains. **No cost-basis data is collected**, so this is an approximation, not a
   gains calculation. Do not describe it as one in user-facing text.
3. Minimize wash-sale exposure: dollars bought, in a shelter, of a fund also held in
   a taxable account.
4. Minimize bonds held in *tax-free* accounts, i.e. put them in tax-deferred space
   and leave Roth/HSA room for stocks.
5. Minimize the international fund held in *tax-advantaged* accounts, i.e. prefer it
   in taxable, where its foreign withholding is claimable as a credit.
6. Tie-break by minimizing total trade volume everywhere, so the plan
   disturbs the fewest positions.

The variable layout is `[ x (n) | y (n) | w (k) ]` — slot values, their absolute
deviations `|x - current|`, and one-sided purchase amounts for phase 3. It is built
once, so an objective is a vector over the same columns and a solved optimum is a row
appended to `A_ub`. An objective with no nonzero coefficient is skipped rather than
solved: it would only re-find a feasible point and carry a vacuous bound.

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
- **Phase 4 does the opposite, deliberately**, and uses `_fund_type_coefficient`.
  Bonds inside a Roth's target-date fund really are bonds occupying tax-free space,
  exactly as phase 1 counts them — and a TDF account is pinned by its own budget row
  anyway, so counting them states the truth without giving the solver anything to act
  on.
- **Phase 3 penalizes only the sheltered *buy* side.** The taxable sale is the leg
  that realizes the loss, but it is also the leg phase 2 has already minimized and the
  one the portfolio usually has no choice about; penalizing it too would put phase 3
  in direct opposition to phase 1, which exists to sell exactly those taxable bonds.
- **A taxable holding of zero creates no wash-sale variable.** You cannot sell what
  you do not own. Without that check an empty taxable slot standing ready to receive a
  fund suppresses the very purchase phase 5 wants to make, over a sale that can never
  happen — `test_an_empty_taxable_slot_does_not_suppress_a_sheltered_purchase` is the
  regression guard, and it caught this during implementation.

Phase 3 cannot condition on whether the taxable side is *actually* sold — that is
decided by the same solve, and no linear objective can express it. The residue is a
mild preference against accumulating, in a shelter, a fund you already hold in
taxable. It costs nothing and usually points the same way as phases 4 and 5. What the
LP cannot avoid, `_wash_sale_warnings` reports after the fact from the final trades.

A caution when testing placement: this LP is degenerate, so a scenario where the
preferred placement merely *ties* proves nothing — the old solver often picked it
anyway. A test earns its keep only if it fails against the previous ranking; see
`test_international_is_moved_out_of_tax_advantaged_when_the_trades_are_free` and
`test_bonds_fill_tax_deferred_space_before_tax_free_space`. Phase 3 is especially
prone to this: the alternative-fund choice is usually volume-symmetric, so HiGHS
often picks the non-overlapping vertex on its own. Its tests are therefore built on
the deterministic paths — an unavoidable overlap that must warn, and the zero-holding
exclusion — rather than on a tie it happens to win.

`_OBJECTIVE_SLACK` exists because HiGHS is not bit-exact — a hard `<=` against a raw
optimum can spuriously reject the next phase's true optimum. Don't set it to zero.
But note that every carried bound is also a *budget a later phase can spend*: giving
up that much of an earlier priority is permitted, and the volume-minimizing phase at
the bottom will take it. At the original cent, with five bounds stacked up, that
surfaced as "sell $5,999.99" where the answer is $6,000.00. It is now a tenth of a
cent — clear of HiGHS's noise (verified from $100k to $8B) but below the cent grid
every displayed amount rounds to, so it cannot produce a visible artifact.

`_check_capacity_feasible` deliberately fails early with an actionable message rather
than letting scipy surface a generic "infeasible". It bounds each asset class by the
smallest and largest coefficient among an account's own slots, because the account
must spend exactly its own total across them — so a single-fund account's floor and
ceiling are the same number. All three ceilings are checked before any floor: one
account holding one fund breaches both at once, and "nothing you hold can be bonds"
points at the missing piece, while "you are stuck holding this much U.S. stock"
describes the same problem from the side the user can do least about.

What has to be reachable is the nearest *band edge*, not the target — a band the
portfolio can satisfy is satisfiable even when the exact target is not, which is one
of the things a band is for. This is the band's **only** remaining role inside the LP:
it is the feasibility envelope, not the destination, so a portfolio that trips the gate
aims at target and is stopped short only by what the accounts can actually hold.
`_band_note` names that edge in the message only when the band is what makes the number
binding; with no band the edge is the target, and printing the same figure twice reads
like a bug.

Trades below `MIN_TRADE_DOLLARS` are dropped as impractical.

### The rebalancing band (`allocation.effective_band_points`)

Two rules, and a class has to satisfy **both**, so the tighter of the two is what
binds — **the 5/25 rule**. `band_pct` is the *absolute band*, in points of the whole
portfolio; `relative_band_pct` is the *relative band*, a share of the asset class's own
target, so it scales with the target where the absolute one does not. Those three names
are what the prompts, the report, the saved keys and the README all say, so a change to
one of them is a change to all five places.

Neither alone works for all three classes. Five points is a quarter of a 20% bond
sleeve and far too loose for a 5% one — five points below a 5% target is *zero bonds*,
which is how a portfolio holding 1.2% against a 5% target was reported as in-band and
left alone. Twenty-five percent of a 58.8% U.S. target is 14.7 points, far too loose
for the class that dominates the portfolio. Taking the lesser gives small targets the
relative rule and large ones the absolute cap. The two cross at a 20% target, where
both come to 5 points — which is why the convention is usually stated as "5 points at
20% and above, 25% relative below": one rule, described twice.

`relative_band_pct` of `None` means the rule was never configured and only `band_pct`
applies. That is **distinct from `0`**, which like a `band_pct` of `0` tolerates no
drift at all. The distinction is what lets `compute_trades`'s `band_pct`-only default
keep meaning exactly what it did — every solver test that says nothing about the band
still asserts exact-target behavior — and it is the same "absent means never chosen"
that `rebalance_band_pct` uses in the config file.

**The relative half is the one question in the flow that gets explained before it is
asked.** Everything else is asked bare and explained where its effect is visible, in
the report. That does not work here: "or by more than this share of its own target"
reads as an alternative when it is a second, tighter limit, and the reason the rule
exists — five points of drift is the whole of a 5% bond sleeve — is invisible from the
prompt. So `prompts.BAND_EXPLANATION` states the policy above the pair, worded the way
one is written in an investment policy statement — an asset class "deviates from its
target" by more than "the lesser of" two bands. That one sentence carries all the
semantics, which leaves each question below naming only its own unit (`pts` against
`%`) — the part that was actually ambiguous. It stays one sentence: drafts that also
named the 5/25 rule, said what the relative band is for and noted that zero turns the
band off were all cut back to what a reader needs in order to answer the two questions.
The report's "Rebalancing band" section is where the band's effect is visible, and it
writes the resulting ranges out per class. The questions are "Absolute
band" and "Relative band": the industry's own names for the two halves, and the words
`rebalance_band_pct` and `rebalance_relative_band_pct` are already named after, so the
prompt, the saved key and the report all say one thing. `TestRebalanceBandPrompts`
holds this.

The vocabulary throughout is the Bogleheads wiki's and Larry Swedroe's, because that
is where a reader checking the defaults ends up: "rebalancing band", "asset class", an
asset class that "deviates" from its target, and the pair of numbers as **the 5/25
rule** — absolute 5, relative 25. The rule is named in `config.py`, the README and this
file rather than in the prompt itself. Where the two traditions disagree, precision
wins: Bogleheads writes the absolute half as "5%", which is 5 percentage *points*, so
the prompt's unit stays `pts`.

Because each class now has its own band, nothing user-facing may name a single number
for it. `report._describe_band` writes the three ranges out; `_describe_band_extent`
is the one place that decides between "your band of plus or minus X percentage points"
(absolute only) and "its rebalancing band" (both rules), and the comparison table's
footnote and the no-trades line both go through it.

### VT allocation (`vt_allocation.py`)

Source chain, tried in freshness order: monthly JSON endpoint → quarterly fact-sheet
PDF (separate host, outside the interactive site's bot protection) → last cached
value → manual entry. **It never guesses silently.** `FALLBACK_VT_US_PCT` is only
ever a *suggested default* in the manual prompt.

The fund page's HTML is deliberately not scraped — client-side rendered behind bot
protection. Don't "improve" this by adding a scraper.

### Output structure (`formatting.py`)

Hierarchy uses two devices only: a rule under a heading, and indentation.
`=` banners the three steps *and the report they produce*, `-` underlines divisions
within either, and below that nesting is position alone — an account is a plain label,
indented, with its contents one level deeper. Resist adding a third rule style; that
was tried and reverted. `format_result_header` is not a third style: same rule, same
width, just no step number.

**Two widths, both following the terminal.** `formatting.prose_width()` is
`min(terminal - 2, PROSE_MAX_WIDTH)`; `formatting.table_width()` is `terminal - 2` with
no cap. They diverge because they want opposite things: a paragraph gets *harder* to
read as it widens, while a table of dollar figures does not. Prose, warnings, notes and
the `=` banners all use prose width; tables are sized to their own contents within the
table budget.

This replaced a fixed 78, which was fine for prose but squeezed the tables — the
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
prose, warnings, account headings, trade lines, the comparison table -- wraps or is
sized to fit, and `test_long_names_do_not_push_prose_or_headings_off_the_page` holds
the line. The exception is deliberate: a fund entered by its real name rather than its
ticker ("Vanguard Total Stock Market Index Fund Admiral Shares") cannot fit alongside
an amount in 78 columns, truncating it is how someone buys the wrong fund at the
broker, and wrapping it destroys the alignment the table exists for. It runs wide and
stays aligned. Nicknames are capped at input instead (`MAX_ACCOUNT_NAME_LENGTH`) --
those are labels the user invents, unlike a fund's real name, and they were what
pushed the headings off the page.

An account heading is always `nickname (type, treatment)` —
`Fidelity Roth IRA (Roth IRA, tax-free)`, `Fidelity Brokerage (Brokerage, taxable)` —
with the treatment *inside* the parentheses. Inside rather than after a dash because it
is shorter and safe at the nickname cap: the longest possible heading lands well inside
the page rather than wrapping and stranding a `--` at the end of a line. Uniform
because one line shaped like the next is what lets the eye compare them down the page.

There used to be a rule suppressing the treatment when the type already named it, for
the sake of the account type then called `Taxable Brokerage`. Since v4 renamed that to
plain `Brokerage`, **no account type names its own treatment**, and the branch was
removed rather than left unreachable. Reintroducing a type that does — a
`Tax-free Savings Account`, say — is what would bring the question back.

### Wording the output has to keep

These are compliance-driven, not stylistic, and an edit that reads better but
loses them is a regression:

- **The report always ends with the disclaimer.** It is the artifact that gets
  screenshotted and acted on days later; a disclaimer that lives only in the README
  does not travel with it. `report.DISCLAIMER` is the one copy — `--help`'s epilog is
  that same object rather than a second wording of it, so the two cannot drift apart
  (`test_help_carries_the_report_s_own_disclaimer`). It is **two clauses**: not
  investment, tax or legal advice, and **not a recommendation to buy or sell** — the
  Reg BI / FINRA 2111 term of art, and the other half of never using the word above.

  **It is two lines, and stays two lines.** A longer draft also disclaimed the advisory
  relationship, order placement and trademark use. All true, all cut: eight lines of
  legal prose at the foot of a page is something a reader learns to skip, which costs
  the disclosure the one thing it is there for. Those clauses are not restated elsewhere
  either: the README's Disclaimer section was cut back to the same two clauses plus a
  pointer to Limitations, and `--version` is now the only place carrying
  non-affiliation. Adding a clause here means finding one to cut.
  `TestRequiredWording` pins both the wording and the line count.
- **Nothing is called a "recommendation" and no order is phrased as an instruction.**
  "Recommendation" is a term of art under Reg BI and FINRA Rule 2111. Hence "Orders to
  place" and "Review each order before placing it:" rather than "Recommended trades"
  and "Place the following orders:". The disclaimer's "not a recommendation to buy or
  sell any security" is the explicit denial that goes with the avoidance.

- **"Order" and "trade" are not synonyms — use the industry split.** An *order* is the
  instruction you submit to a broker; a *trade* is the transaction that results, and
  the activity in general. So: "Orders to place", "Review each order before placing
  it", "before placing these orders", "the above orders do not reach the target",
  "once these orders are filled" — all instructions. And: "no trades needed", "the
  trades needed to rebalance", "taxable trade volume" — all activity or outcome. The
  giveaway is the verb: you *place*, *submit* and *fill* an order; you *make* a trade
  and live with its result.

  Note that "not yet submitted" does **not** make something a third kind of thing.
  Everything under "Orders to place" is an order that has not been placed, so a
  sub-minimum one that was dropped is simply an order missing from the list — "One
  order smaller than $1.00 was left out", not "one move". A third noun for the same
  object is a vocabulary the reader has to learn for no gain. It does force "so the
  above orders do not reach the target exactly" at the end of that sentence: with a
  dropped order named in the same breath, "these orders" points at either set.

  Code identifiers stay on "trade" (`Trade`, `compute_trades`, `MIN_TRADE_DOLLARS`)
  because in portfolio-rebalancing systems the computed output is a *trade list* --
  "order" belongs to the execution layer this program never reaches. That is a
  deliberate split, not an oversight.
- **Tax statements are conditional and attributed.** The wash-sale warning says a sale
  "may be" a wash sale and that "the IRS has taken the position (Rev. Rul. 2008-5)"
  -- never that it *is* one or that a loss *is* lost. The tool cannot see cost basis,
  trade dates, or purchases made elsewhere in the 61-day window. It also names the
  rule it is talking about -- section 1091, "substantially identical" securities,
  "within 30 days before or after the sale, in any account you control" -- because
  without the standard, "funds are matched by name here" is a caveat about nothing in
  particular, and the reader has no way to judge whether their own replacement fund is
  far enough away.
- **A taxable sale is disclosed as a taxable event.** `report._describe_taxable_sales`
  says it "may realize capital gains or losses" and that no cost basis is collected.
  Phase 2 minimizes taxable *volume*, which is not the same as pricing the sale, so the
  wording must neither skip the disclosure nor imply the solver costed it. Only the
  sale leg triggers it; a taxable buy realizes nothing.
- **The landing allocation is conditional on the orders filling.** "If filled at the
  values you entered", not "After these trades": an order fills at the market's price
  on the day, not at the figure typed into the prompts, so the number is arithmetic
  rather than a promise.
- **"Tax-free" is qualified once**, under "Your accounts", because Roth and HSA
  withdrawals are tax-free only when qualified. One line: naming the age,
  holding-period and medical-expense conditions is the reader's plan documents' job,
  not a rebalancer's.
- **A prompt that classifies tax treatment says when the tax is paid, accurately.**
  The "Other" account's three choices are the only place the program explains the
  distinction, and they had said gains in a taxable account are taxed "every year".
  They are also printed unwrapped by `prompt_choice`, so each has to fit
  `prose_width()` -- `TestTaxTreatmentChoices` holds both lines.
- **No claim implies future performance.** The asset-location note calls preferring
  tax-deferred space "a common asset-location convention, not a prediction" rather than
  asserting that stocks will out-grow bonds.
- **Figures carry their provenance** -- "Values as entered, not live market prices.",
  plus "Last saved <date>." as its own sentence when they came from a config file. The
  numbers are the user's, and can be stale.
- **Dropped sub-minimum moves are disclosed**, so trades that do not reach the target
  exactly are explained rather than looking like an arithmetic error. The count is
  spelled out through nine (`report._count`): the sentence opens on it, and "1 order
  smaller than $1.00 was left out" reads as a fragment rather than a sentence.

**Indentation is carried by `Prompter.indented()` and `INDENT_UNIT`, never spelled
into a prompt string.** `_prompt_target_date_allocation` and `_prompt_new_holding` are
each called from two places at different depths, so a literal `"    "` that lines up
in one lands two levels off in the other -- which is exactly what happened, and what
let a `say_wrapped` conversion silently drop a line four columns out from its own
siblings. Every level steps by exactly one `INDENT_UNIT`.
`TestIndentation` pins the report's depths.

**Percentages follow two rules, one per side of the program.** The report fixes every
percentage at **one decimal place** -- whole numbers included, because "20% bonds" two
lines under "20.0%" is exactly the inconsistency the rule exists to stop. Prompts and
echoed-back values do the opposite and **trim trailing zeros** via
`formatting.format_percent`, so a default reads the way someone would type it and one
prompt never offers `[80]` while the next offers `[62.0]`. `prompt_percent` is the
single door for asking one: it owns the 0-100 bounds, the `(%)` suffix, and the default
formatting.

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

**The orders close with where they land** (`_describe_outcome`) — the question the
rest of the report only answers by implication. It is computed from the holdings
rather than the class totals, so a trade in a target-date fund moves all three sleeves
by their own fractions, and it is stated conditionally ("If these orders fill at the
values you entered") for the reason in the wording section below. The disclosures that
follow it — taxable sales, then costs — sit between it and the warnings, so everything
qualifying the orders is in one run rather than split across the page.

The report restates every answer it was given — target allocation and where it came
from, the band, the accounts and their holdings — before the current-vs-target summary
and the trades. Read on its own with no scrollback it should still say what was asked
for and what to do. `RebalanceInputs` carries that set, so recapping one more answer
does not mean growing `format_report`'s signature again.

`Prompter.indented()` carries depth for interactive output, so indentation is a
property of where you are in the flow rather than something spelled into each string.
`_at_depth` intentionally leaves leading blank lines flush — several messages open
with `\n` as a separator, and padding it would emit trailing whitespace.

**`report.py` must not import `prompts.py`.** Shared presentation constants
(`INDENT_UNIT`) live in `formatting.py`, which both import.

### Persistence

`~/.three_fund_rebalance/config.json`, versioned by `SCHEMA_VERSION`, written
atomically (temp file + `os.replace`). Saved values are re-offered as *editable
defaults*, never silently trusted.

**Every way a config file can fail to load raises `PersistenceError`** — that is
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
blank as it does for any `PersistenceError`. That is deliberate — splitting such an
account automatically would invent an account boundary that is a hard constraint on
the solver.

The file is at v4, and upgrades run **one hop at a time** — `config_from_dict` chains
`v1 → _upgrade_v1 → v2 → _upgrade_v2 → v3 → _upgrade_v3 → v4`, so a v1 file walks the
same path a v3 file does. Each upgrade translates without validating: anything still wrong surfaces from
the normal parse, so a corrupt old file reports what a corrupt current file would.
Each copies at every level, because a failed load must not leave the caller's parsed
JSON half-renamed. Any further rename of a persisted name needs another hop, not an
in-place edit of an existing one — and `_upgrade_v1` must keep returning `2`, not
`SCHEMA_VERSION`, or it will skip every hop added after it.

- **v1 → v2** spelled the fund types after the academic asset classes
  (`domestic_equity`, `tdf`, `balance`, `balances_as_of`); v2 uses the same words the
  CLI prints (`us_stock`, `target_date`, `value`, `values_as_of`).
- **v2 → v3** splits the single `tax_advantaged` treatment into `tax_deferred` and
  `tax_free`, re-inferred from the account's own persisted `account_type` via
  `ACCOUNT_TYPE_TAX_TREATMENT`. An unrecognized type — including `"Other"`, whose v2
  answer was a yes/no that never recorded the difference — becomes `tax_deferred`:
  bonds fill that space first, so guessing this way costs nothing if it is wrong.

  Note `_upgrade_v2` looks types up in the *current* `ACCOUNT_TYPE_TAX_TREATMENT`, which
  no longer holds v2's spellings. That is safe only because the lookup runs solely for
  accounts marked `tax_advantaged`, and the one type v4 renamed is taxable. A rename
  that touches a shelter will need `_upgrade_v2` to carry its own frozen v2-era map.
  `rebalance_band_pct` is deliberately left *absent* rather than defaulted, because
  absent means "never chosen" and the step 2 prompt offers the default; writing one in
  would make a guess look like the user's own saved answer.

- **v3 → v4** renames the `Taxable Brokerage` account type to `Brokerage`. Every other
  entry on the list is the account's actual name — Roth IRA, 403(b), HSA — while
  "Taxable" is a descriptor, and Title-Casing it put the one word the report otherwise
  always writes lowercase (beside "tax-free" and "tax-deferred") into a proper noun. An
  account type the map does not know, `"Other"` included, is left exactly as it is.

`rebalance_relative_band_pct` was added later **without a hop**, and deliberately: a
new optional key translates nothing, and its absence already means "never chosen"
exactly as an absent `rebalance_band_pct` does. A hop is for a name or a meaning that
changed. Note that `_upgrade_v2` now writes the literal `3` rather than
`SCHEMA_VERSION` — same trap as `_upgrade_v1`, harmless only until the next hop
exists.

## The README

It answers "what will this print, and what will it not do" — for someone deciding
whether to install it. Everything about *how* the code works lives in this file
instead, and the two must not converge: a README that grows a solver-phase
explanation is the failure mode to watch for.

Sections, in order: the one-paragraph blurb, Disclaimer, Example, Install, Running,
How it works, Limitations, Development, License.

**The Example is real output, pasted verbatim.** It is the first thing a reader sees
and the reason the README is structured around it, so it may never be hand-edited or
hand-idealized — re-generate it and paste the result. Any change to `report.py` or
`formatting.py` wording means re-generating it. To do that, drive `run()` with a
scripted prompter as the tests do (never by piping stdin — see "Running the CLI
without side effects"), under `COLUMNS=80`, which is what `tests/conftest.py` pins the
suite to and therefore the width every wrapping assertion in the repo assumes. The
scenario is 80/20, a 5/25 band, and three accounts: a Brokerage holding VTI and VXUS,
a Roth IRA holding VTI plus a declared-but-empty BND, and a Traditional 401(k) holding
VTI and BND.

**One line of the Example cannot come from such a run.** Passing `--vt-us-pct` to skip
the network stamps the provenance line "manually specified via --vt-us-pct"
(`cli.py`), where the README shows the fetched form — `_format_as_of`'s
`%B %-d, %Y`, e.g. "(June 30, 2026)". The README deliberately shows the fetch path,
because that is what a reader running the CLI normally will see. Substitute that one
line by hand and leave the other seventy-odd exactly as printed.

**How it works is a list of bolded lead-ins, each followed by at most a short
paragraph** — two to four lines. It is a summary, not a specification. An entry that
needs more room is either two entries (the band's definition and the band's trigger
semantics are split for exactly this reason) or a Limitations bullet. Growing one past
a short paragraph is the thing that keeps happening; splitting it is the fix.

**Limitations is where caveats go**, as bullets with bolded lead-ins, which is what
lets How it works stay short. A newly discovered thing the tool cannot see is a bullet
there, not a qualification bolted onto a paragraph above.

**The Disclaimer section is `report.DISCLAIMER`'s two clauses plus a pointer to
Limitations, and nothing else.** The clauses cut from the report — advisory
relationship, order placement, trademark use — are not restated here either; see the
disclaimer entry under "Wording the output has to keep" for why. Non-affiliation lives
in `--version` alone.

**Every name the README uses for a user-visible concept is the program's own name for
it.** The two bands, the 5/25 rule, the order/trade split, and the ban on
"recommendation" all apply here exactly as they do to printed output — the README is
one of the places the band names have to agree, and a rename is a change to all of
them at once.

Mechanically: prose wraps at 78 columns, hard; `--` for a dash, never an em dash, so
the source matches what the CLI prints; asterisk emphasis for the band names on first
use. Only an unbreakable line inside a fence (a `pipx install` URL) may run past 78.

## Testing conventions

Test files mirror modules 1:1; tests are grouped in `Test*` classes by the function
under test. Names are full sentences describing the behavior.

`Prompter` takes injected `input_func`/`print_func`, so the entire interactive flow is
driven by a scripted list of canned answers — **no monkeypatching of builtins**.
`tests/test_cli.py::ScriptedPrompter` and `new_account_responses()` are the helpers;
reuse them rather than writing new stdin plumbing. Adding a question to the flow means
threading one more answer into every scripted list — the band's two answers sit
between the VT allocation and the first "Add an account?", and existing flows pass
`"0"` for both so they keep testing exact-target behavior.

`compute_trades`'s `band_pct` defaults to `Decimal(0)`, which is the exact target and
therefore the pre-band behavior — solver tests that aren't about the band say nothing
about it and keep asserting the same numbers. `relative_band_pct` defaults to `None`
for the same reason: `0` there would collapse every one of those tests onto the exact
target by a different route, and silently.

Every network call is monkeypatched, including failure paths. The suite must stay
runnable offline — CI depends on it.

## Gotchas

`pyproject.toml` configures no ruff `select`, so the active rule set is whatever the
resolved ruff version enables by default, and `dev` pins only `ruff>=0.6`. With 0.16.3
that default includes isort (`I001`) and flake8-simplify (`SIM117`) — import order
*is* enforced, and a newer or older ruff can change what CI flags.

Version lives in `three_fund_rebalance/__init__.py`; `pyproject.toml` derives it via
`dynamic = ["version"]`. Bump it in one place.

`license-files` in `pyproject.toml` is PEP 639 and requires `setuptools>=77`, which is
why the build-system floor is set there.
