---
paths:
  - "tests/**/*.py"
---

Read `docs/testing.md` before adding or changing a test -- it carries the
scripted-prompter conventions and the exact shapes of canned answers, and a test
written without them tends to look right and assert nothing. Every network call is monkeypatched and the suite must stay
runnable offline; `tests/test_network_sources.py` is the one deliberate
exception, deselected by default.
