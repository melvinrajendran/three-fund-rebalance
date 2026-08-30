---
name: release
description: Cut a release of three-fund-rebalance -- run the live network tests, bump the version, tag and push.
disable-model-invocation: true
argument-hint: [version]
---

# Cut a release

The package is on PyPI as `three-fund-rebalance`, and the README's install
instructions name it rather than a git URL. A release is a tag.

## Steps

1. **Run the live sources.**

   ```bash
   pytest -m network
   ```

   This is a release step, and this is the moment it exists for. The default
   suite mocks every network call, so a rotted VT source is invisible to CI and
   to every contributor until a user runs the CLI and quietly gets the fallback.
   Cutting a release is the one scheduled moment when someone is paying
   attention, so it is where the check belongs.

   A failure here is not automatically a bug in this repo -- Vanguard may just be
   down -- but it must be understood before tagging, not after. The alternative is
   shipping a version whose primary source does not exist, which is exactly what
   0.1 through 0.5 did. **Stop and report a failure to the user; do not tag
   through it.**

2. **Run the default suite and the linter.**

   ```bash
   pytest && ruff check three_fund_rebalance tests
   ```

3. **Bump the version in one place** -- `__version__` in
   `three_fund_rebalance/__init__.py`. `pyproject.toml` derives it via
   `dynamic = ["version"]`.

4. **Commit the bump**, then tag and push:

   ```bash
   git tag v0.5.0 && git push origin v0.5.0
   ```

   The tag must equal `__version__`; `publish.yml`'s first step asserts it,
   because the tag is otherwise a second place to get the version wrong and a
   mismatch would publish a version nobody can `git checkout`.

## What happens then, and what can go wrong

`.github/workflows/publish.yml` fires on `v*`, builds an sdist and a wheel, and
uploads them through **PyPI Trusted Publishing** -- PyPI mints a short-lived token
from the workflow's OIDC identity, so there is no API token in the repo or in
GitHub secrets.

What makes that work lives outside the repo and is invisible from inside it: a
publisher registered on PyPI for owner `melvinrajendran`, repository
`three-fund-rebalance`, workflow `publish.yml`, environment `pypi`, plus a GitHub
environment of that same name. All four have to match or the upload is rejected.

**A version, once uploaded, can never be replaced or reused**, even after a
delete. A botched release is fixed by bumping to the next version and tagging
again, never by re-cutting the same one.

`pytest -m network` is deliberately **not** a step in `publish.yml`. Two reasons,
and the second decides it. A third-party outage would block a release for a
reason that has nothing to do with the release. And the tests would run from a
GitHub runner's datacenter IP, which is precisely the kind of client the
interactive site's bot protection treats differently from a laptop -- so a failure
there would be ambiguous in the one place ambiguity is most expensive. Run it
locally, where a failure means what it says.
