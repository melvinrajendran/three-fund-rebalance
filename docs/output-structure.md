# Output structure (`formatting.py`)

Part of the notes in [`CLAUDE.md`](../CLAUDE.md).

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
