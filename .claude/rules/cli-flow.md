---
paths:
  - "three_fund_rebalance/cli.py"
  - "three_fund_rebalance/prompts.py"
  - "tests/test_cli.py"
---

Read `docs/cli-flow.md` before changing this file -- it is normative, not
background, and covers the revise loop, the update menu and the summary file.
Note the menu names each question by `prompts`'s own subheading constants
rather than a paraphrase, and everything it can reach is re-asked by the same
function the numbered step used.
