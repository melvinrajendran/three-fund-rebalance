# The rebalancing band (`allocation.effective_band_points`)

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

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
