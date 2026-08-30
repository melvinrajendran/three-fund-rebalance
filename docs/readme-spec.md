# The README

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

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
