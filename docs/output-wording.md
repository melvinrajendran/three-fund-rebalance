# Wording the output has to keep

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

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
  fires only where that bound is provably the obstacle; see [`solver.md`](solver.md).

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
values entered here") for the reason under "The landing allocation is conditional on
the orders filling" above. **It is indented to
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
