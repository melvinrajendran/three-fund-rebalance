---
paths:
  - "three_fund_rebalance/report.py"
  - "three_fund_rebalance/formatting.py"
  - "three_fund_rebalance/prompts.py"
  - "tests/test_report.py"
  - "tests/test_formatting.py"
  - "tests/test_prompts.py"
---

Two documents govern this code, and both are normative:

- `docs/output-wording.md` -- what the program is allowed to say. Several rules
  there are compliance-driven (Reg BI, FINRA 2111): nothing is a
  "recommendation", tax statements stay conditional, the report always ends on
  the disclaimer, and the output never addresses the reader's holdings in the
  second person. An edit that reads better but loses one of those is a
  regression.
- `docs/output-structure.md` -- how the page is laid out: the two widths, the
  rule-and-indentation hierarchy, Title Case subheadings, aligned money columns.

Changing report or formatting wording means the README's Example is stale; see
the `/readme-example` skill.
