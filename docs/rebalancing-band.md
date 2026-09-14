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

**Both halves are always set.** There is no "absolute band only" state: the prompts
always ask for both, so a `None` relative band was a code path the program could never
reach, and it was removed. A `0` on either half tolerates no drift at all.
`compute_trades` and `summarize_allocation` default to a `band_pct` of `0` (the exact
target, whatever the relative half says) and a relative band of
`DEFAULT_REBALANCE_RELATIVE_BAND_PCT`. To state the absolute rule alone, pass a
relative band of `100`: `min(band, target)` is the band wherever the band is no
larger than every target. The config file is a different matter: an absent
`rebalance_relative_band_pct` there still means "never chosen", exactly as an absent
`rebalance_band_pct` does, and the prompt offers the default.

**Both halves default to the 5/25 rule, and the user can type over either.**
`prompt_rebalance_band` and `prompt_relative_rebalance_band` offer a *saved* answer
when there is one and otherwise fall back to `DEFAULT_REBALANCE_BAND_PCT` /
`DEFAULT_REBALANCE_RELATIVE_BAND_PCT`, so a first run can accept the convention with
Enter and a returning user keeps what they chose. This reverses an earlier decision to
require both answers on a first run, at the user's request. The config file still stores
an absent band as absent -- the default is applied at the prompt, never written in on
load. `TestRebalanceBandPrompts` pins both halves: 5/25 on a first run, a saved answer
preferred over it, and either one overridable.

**The README states the defaults but does not name the 5/25 rule.** "They default to
5 points and 25%" is what a reader needs to know what they get by pressing Enter; the
rule's name is not.

**The relative half is one of the two questions in the flow that get explained before
they are asked** (the other is which funds an account lists -- see `prompts.FUND_EXPLANATION`).
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
rule** -- absolute 5, relative 25. The rule is named in `config.py` and this file; the
prompt and the README show its numbers as the defaults but not its name.
Where the two traditions disagree, precision wins: Bogleheads writes the absolute half
as "5%", which is 5 percentage *points*, so the prompt's unit stays `pts`.

**The comparison table shows both drifts, and the `*` sits on the one that crossed its
rule.** A single `Drift (pts)` column starred bonds at 3.5% against 5% as `-1.5 *`, which
reads as inside a 5-point band -- it was starred for being -30% relative. So the table has
an Absolute Drift column (points) and a Relative Drift column (a share of the class's target, `--` for a 0% target), each with its own
marker slot. `CategorySummary.outside_absolute` / `outside_relative` say which rule a
class crosses; `within_band` still comes from `effective_band_points`, and
`test_within_band_is_the_two_rules_together` holds the two to one answer.

Because each class has its own band, nothing user-facing may name a single number for
it. `report._describe_band` writes the three ranges out, and the comparison table's
footnote and the no-trades line both say "its rebalancing band".

**The no-trades line has to survive being read against the starred rows above it.**
Nothing to trade and a class still outside its band is neither "already matches the
target allocation" nor "every asset class is within its band" -- it is what the accounts
can hold that stopped it, so a third line says so. It reads `within_band` off
`summary.categories` rather than anything the solver reports: the summary describes the
current holdings, which with no trades are also the final ones, so it is right by
construction even where `_capacity_notes` has nothing it can truthfully say.
