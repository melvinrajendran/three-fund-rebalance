"""Rules that CLAUDE.md and docs/ state in prose, checked mechanically.

Everything here was a sentence in the notes and nothing else. A rule the suite
enforces survives a refactor that a rule only written down does not.
"""

import ast
import re
from pathlib import Path

PACKAGE = "three_fund_rebalance"
ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / PACKAGE


def _internal_imports(module_name: str) -> set[str]:
    """The names of sibling modules that `module_name` imports."""
    tree = ast.parse((SOURCE_DIR / f"{module_name}.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from three_fund_rebalance.models import ...`
            if node.module and node.module.startswith(f"{PACKAGE}."):
                imported.add(node.module.split(".", 1)[1])
            # `from three_fund_rebalance import models`
            elif node.module == PACKAGE:
                imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{PACKAGE}."):
                    imported.add(alias.name.split(".", 1)[1])
    return {name for name in imported if (SOURCE_DIR / f"{name}.py").exists()}


def _rule_paths(rule: Path) -> list[str]:
    """The globs in a rules file's `paths:` frontmatter.

    Parsed by hand rather than with a YAML library: the frontmatter here is a
    list of quoted scalars and nothing else, and a test dependency that the
    package itself does not have is a worse trade than eight lines.
    """
    text = rule.read_text()
    if not text.startswith("---\n"):
        return []
    frontmatter = text.split("---\n", 2)[1]
    return re.findall(r'^\s*-\s*"([^"]+)"\s*$', frontmatter, re.MULTILINE)


class TestImportGraph:
    """The imports form a DAG with `models` at the bottom and `cli` at the top."""

    def test_models_is_the_bottom_of_the_dag(self):
        """It holds the dataclasses everything else passes around, so it may
        depend on nothing of ours."""
        assert _internal_imports("models") == set()

    def test_report_does_not_import_the_solver(self):
        """`report` renders a `RebalanceResult`, which lives in `models` alongside
        `Trade` for exactly that reason -- so the reporting layer depends on the
        data and not on the scipy-backed solver that produced it."""
        assert "rebalance" not in _internal_imports("report")

    def test_report_does_not_import_the_prompts(self):
        """Shared presentation constants live in `formatting`, which both import."""
        assert "prompts" not in _internal_imports("report")

    def test_nothing_imports_the_cli(self):
        """`cli` orchestrates the pipeline end to end and is therefore the top."""
        for path in sorted(SOURCE_DIR.glob("*.py")):
            if path.stem in {"cli", "__init__"}:
                continue
            assert "cli" not in _internal_imports(path.stem), path.name


class TestReadmeMechanics:
    """The README's prose wraps at 78 columns, hard, and uses `--` for a dash."""

    README = ROOT / "README.md"
    MAX_WIDTH = 78

    def test_no_line_exceeds_78_columns_except_the_options_table(self):
        """Only an unbreakable line may run past 78: a row of the options table
        under Running, which cannot be wrapped without breaking the table."""
        too_long = [
            (number, line)
            for number, line in enumerate(self.README.read_text().splitlines(), start=1)
            if len(line) > self.MAX_WIDTH and not line.startswith("|")
        ]
        assert too_long == []

    def test_the_options_table_is_the_only_thing_that_runs_wide(self):
        """The carve-out above is for a real exception, not a blanket pass for
        every line starting with a pipe. If no table row is over the width any
        more, the exception has stopped earning its keep."""
        wide_rows = [
            line
            for line in self.README.read_text().splitlines()
            if line.startswith("|") and len(line) > self.MAX_WIDTH
        ]
        assert wide_rows, "no wide table rows left -- drop the exception above"

    def test_no_em_dash(self):
        """`--` for a dash, never an em dash, so the source matches what the CLI
        prints."""
        assert "—" not in self.README.read_text()


class TestRuleScoping:
    """The `.claude/rules/` stubs load `docs/` by path, and nothing checks that
    from inside a session.

    A `paths:` glob that stops matching fails silently: the rule simply never
    loads again, the document it points at goes unread, and no test, lint or
    runtime error says so. That is the one failure this split introduces which
    the pre-split single file could not have, so it is checked here.
    """

    RULES_DIR = ROOT / ".claude" / "rules"
    DOCS_DIR = ROOT / "docs"

    def test_every_paths_glob_matches_at_least_one_file(self):
        """A rule scoped to a module that has been renamed or removed is a rule
        that never loads again."""
        dead = [
            (rule.name, pattern)
            for rule in sorted(self.RULES_DIR.glob("*.md"))
            for pattern in _rule_paths(rule)
            if not list(ROOT.glob(pattern))
        ]
        assert dead == []

    def test_every_rule_has_at_least_one_paths_glob(self):
        """A rules file with no `paths:` is always loaded, which defeats the
        point of moving the prose out of `CLAUDE.md`."""
        unscoped = [
            rule.name for rule in sorted(self.RULES_DIR.glob("*.md")) if not _rule_paths(rule)
        ]
        assert unscoped == []

    def test_every_docs_reference_resolves(self):
        """`CLAUDE.md`, the rules and the documents themselves all cross-refer by
        path. A renamed document breaks those pointers silently."""
        sources = [ROOT / "CLAUDE.md", *self.DOCS_DIR.glob("*.md"), *self.RULES_DIR.glob("*.md")]
        broken = sorted(
            {
                (source.name, reference)
                for source in sources
                for reference in re.findall(r"docs/[a-z0-9-]+\.md", source.read_text())
                if not (ROOT / reference).exists()
            }
        )
        assert broken == []

    def test_claude_md_points_at_every_document(self):
        """The always-on file is what tells a session that a deep document exists
        at all -- a path-scoped rule only fires once a matching file is read, so
        a document `CLAUDE.md` never names is invisible to a session that plans
        before it opens anything."""
        index = (ROOT / "CLAUDE.md").read_text()
        unlisted = [
            document.name
            for document in sorted(self.DOCS_DIR.glob("*.md"))
            if f"docs/{document.name}" not in index
        ]
        assert unlisted == []
