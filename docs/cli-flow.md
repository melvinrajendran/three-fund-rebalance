# The CLI flow after the report

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

## The revise loop

The pipeline `cli.run()` orchestrates is in [`CLAUDE.md`](../CLAUDE.md).
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

## Three numbered steps, and the report is not a fourth

The user walks **three** numbered steps -- target allocation, rebalancing band,
account holdings. The report is not a fourth: it is what those produce, so it gets
`format_result_header` (same `=` rule, no "STEP x OF y") rather than a step banner.
`cli._INPUT_STEPS` is the count, in one place.

## The summary file (`--write-summary`)

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
