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

**"Save Portfolio" opens by saying when the file was last written** -- "Last
saved August 29, 2026 at 9:03 PM EDT." above the question, with a blank line
between them the way step 2 sets its explanation off from the questions under
it. The sentence used to sit in the report, under the portfolio total, as the
second half of "Values as entered, not live market prices." It dates the
*file*, though, not the figures, and the one decision it informs is the one
being asked here: whether to overwrite it. Printed only when a file was
loaded, so a first run and a `--fresh` one show the heading and the question
alone.

The stamp is `PersistedConfig.values_as_of`, a local ISO timestamp written to
the second, with `values_as_of_zone` beside it because ISO 8601 has nowhere to
put "EDT". `formatting.format_saved_at` rebuilds the sentence from the pair
through the same `format_generated_at` the report's own first line uses -- so
the two stamps in one session are spelled alike, and a file reads the same on
every machine that opens it rather than being re-zoned to whoever is looking.
A file written before the stamp carried a clock holds a bare date and prints
as one.

**`--no-save` and `--write-summary` govern different files**, which is most of
why the new flag is not called `--save-summary`: beside an existing `--no-save`
that reads as its opposite number, and it is not. `--no-save`'s help now names
the portfolio file outright for the same reason, and the README says it in a
sentence, since a help string is not where someone resolves a confusion they
have not had yet.

## Running without input (`--no-input`)

Everything above is the interactive flow, and `--no-input` is the one way past
it: the saved portfolio is read, the plan is computed, the summary is printed,
and nothing is asked. It exists because a saved answer is re-offered as an
*editable default* and never silently trusted (see
[`persistence.md`](persistence.md)) -- which is right at the prompt and leaves
no way at all to re-run a portfolio that has not changed. Pressing Enter
through three steps and one question per fund per account is not a
non-interactive mode; it is the interactive one, typed faster.

**The name is not `--yes`.** `-y` means "assume yes to confirmations", and the
questions here collect values: there is no yes to assume for "Fund name or
ticker". `--no-input` is the name the same behavior carries elsewhere (pip,
Django, cookiecutter, Terraform's `-input=false`), and it carries the failure
mode with it -- a value the file cannot supply is an error, never a guess.
`--no-input --fresh` is refused by `parser.error`: one reads the saved
portfolio and the other ignores it, so together they ask for a run with no
answers at all.

**The three steps are not printed, not merely unasked.** The report already
restates the target, the band and every account holding, so the inputs are
still on screen -- after the plan rather than before it. What is printed above
them is one line naming the file and its stamp: `Using the portfolio at
~/.three_fund_rebalance/config.json, last saved August 29, 2026 at 9:03 PM
EDT.` It says "at" rather than "saved to" because the stamp behind it is the
only save the line describes: two of the word read as two different events,
the file being written and the figures in it being entered. That stamp
otherwise appears only under "Save Portfolio", which this run never reaches, and it is the only thing separating a plan computed from
months-old balances from one typed in just now. It is built by the same
`format_saved_at` the save section uses, so a file with a bare date prints one
and a file with no stamp says only which file it is.

**There is no revise loop.** The loop exists because a typo is noticed in the
report and nowhere earlier; nothing was typed this run, and the way to change a
saved answer is to run without the flag. A `RebalanceError` therefore returns 1
where the interactive path would offer the menu.

**It implies `--no-save`.** The answers came out of the file, so a save would
rewrite nothing but `values_as_of` -- and that stamp is exactly what the line
above is for. Refreshing it on a run no one looked at is how it comes to date
the last *run* rather than the last time anyone confirmed a figure, which is
the one question it is asked. `--write-summary` is unaffected and still writes:
it already asks nothing, and the two together are the unattended run this flag
is for.

**What it refuses rather than defaults.** A missing file, an unreadable one, and
a file with no `stock_pct`/`bond_pct` each end the run with a message naming the
file -- inventing a stock target is inventing the plan, and the
warn-and-start-blank path has nowhere to go with no prompts. Note a missing file
is checked for by name: `load_config` returns a blank config for one, which is
right for a flow about to ask for everything and wrong for one that cannot ask
for anything. The VT split is resolved flag, then lookup, then cache -- each of
the interactive path's questions answered the way its own default answers it --
and refused if all three come up empty, because `FALLBACK_VT_US_PCT` is only
ever a suggested default at a prompt.

**The bands are the one gap that is filled.** An absent band means "never
chosen" and step 2 offers `DEFAULT_REBALANCE_BAND_PCT` /
`DEFAULT_REBALANCE_RELATIVE_BAND_PCT` for it; using them here is the answer a
user pressing Enter would have given, not a guess at one they typed.

`_collect_answers` holds the three steps and `_answers_from_config` reads them
off the file, so `run()` chooses between them once rather than testing the flag
at each of six places. Every test of this flag hands the prompter an **empty**
answer list, so a question of any kind fails the test outright -- that is the
claim the flag makes, and the only way to test it is structurally.

## An account's funds are a list the user builds

Step 3 no longer asks which *kind* of account this is and then walks fixed slots.
It loops: a fund's name, which of four things it holds, its mix if that is "a mix of
asset classes", and its value -- then "Add another fund?", defaulting to no, as
"Add another account?" does once one account exists.
`prompts.FUND_EXPLANATION` is said above each account's list -- once per account, since
"this account" means the one just named -- for a new account and for a saved one that
has no funds yet, and not for one whose funds are being re-confirmed:
`prompt_accounts` has already said how a saved answer is kept, and an instruction
repeated under every account every run says nothing the run before it did. It starts on
the line directly beneath the heading (or "Keep this account?"), and "Fund name or ticker"
follows it directly.

**Nothing inside an account is set apart by a blank line** -- not the explanation, and
not one fund from the next. Depth already groups a fund's questions under its name; the
blank line goes before each account heading, the one division a reader scanning step 3
needs to find.

The nickname's rule -- `(must be unique, e.g. 'Vanguard Roth IRA')` -- is said with the
first account of a pass only, the same once-is-enough treatment the account types get.

A saved account's funds are walked first, each behind **"Keep this fund?"** -- the same
gate, the same default and the same `Removed '<name>'.` that `prompt_revise_account`
puts in front of an account. It is the only way to drop a fund from a list nothing
else bounds, which is why it is worth a question per fund per run. The account-level
gate answers "is this account still mine"; the fund-level one answers "is this fund
still in the lineup", and a user who changes neither presses Enter through both.

A kept fund is **confirmed, not re-asked**: `Keep VTI?` stands where the name question
would, and one level deeper come the same `Saved details: …` and "Use these details?"
any known fund gets (below), then the value. Its name is not asked, so a changed ticker
is a removal and an addition. Declining the details asks the kind with the saved one as
the default, so replacing an index fund with a balanced one is still one keystroke
rather than a removal and a re-entry. A fund whose kind changes *to* multi-asset has no
saved mix and is asked for one outright -- the same branch a brand new fund takes. One
that stays multi-asset has its mix asked outright too, each saved sleeve offered as the
default: declining the details has already asked for a change, and a second
"Update this fund's underlying allocation?" yes/no would ask for it again.

The update menu reaches all of this through `prompt_revise_account`, unchanged: it is
still the account that is picked from the menu, and the fund list is walked inside it.
Its entry for adding accounts is `Add Accounts`, the one heading `prompt_add_accounts`
prints whether or not any accounts were saved; the question beneath it ("Add an
account?" or "Add another account?") is what says which.

## A fund is remembered by its name

A fund name maps to one kind and mix across every account, and every fund an account
holds is saved -- `cli.run()` builds a `FundCatalog` from the saved file and threads it through
`prompt_accounts`, `prompt_add_accounts` and `prompt_revise_account` down to
`_prompt_holding`, which records every answer back into it.

**A fund removed from every account is forgotten when the run is saved.** Within the
run the catalog still knows it, so a fund moved from one account to another in the
same session -- removed here, added there -- is still recognized; only the save drops
what no account holds (see [`persistence.md`](persistence.md)).

**A name the catalog knows is confirmed, not re-asked.** Kept from the saved file,
typed into a second account, or re-added later in the run it was removed in, `_prompt_holding` says
the fund's kind -- `Saved details: Multi-asset fund`, or `Saved details: U.S. stock fund`,
named by `ASSET_CLASS_LABELS` as the report names funds -- and, for a multi-asset fund,
its mix one level deeper as `formatting.format_fund_mix`, the table the report sets
under the same fund:

```
    Keep VBIAX? [Y/n]:
      Saved details: Multi-asset fund
        U.S. stocks           60%
        International stocks   0%
        Bonds                 40%
      Use these details? [Y/n]:
```

The name is not repeated; it is the line directly above. It then asks "Use these details?", defaulting to yes; yes goes straight to the value. No
asks the kind and mix with the saved answer as defaults. The details shown are the
catalog's, not a holding's copy, and a known fund is stored under the catalog's spelling
of its name -- `vti` typed into a second account is `VTI` there too, rather than a second
spelling of one fund in the plan.

**Once per pass.** A fund's details are shown or asked the first time it appears in a
pass, and every later appearance goes straight to its value: there is one set of
details, so asking about them again in the next account could only offer the same
answer. A pass is one call to `prompt_accounts` -- the saved accounts and the new ones
together -- and `_prompt_holding` tracks it in a `confirmed` set of name keys threaded
beside the catalog. Each pass through the update menu starts a fresh set, which is what
keeps the menu the way to correct a fund's details after the report. A fund dropped at
"Keep BND?" was never confirmed, so re-adding it elsewhere still shows its details.

**A change is never applied silently.** Answering a known fund differently says
`This changes VBIAX's details in every account that holds it.` -- and it does:
`prompt_accounts` returns `catalog.resolve(accounts)`, and `_revise` resolves
`answers.accounts` after every pass, so an account asked earlier picks the change up.

The value is never offered from another account. It is the one thing that is per
account, and a default copied from a different account would be a plausible wrong
number at the prompt a typo is least likely to be noticed at.
