#!/usr/bin/env bash
# PostToolUse hook: lint a just-edited Python file with the same rule set CI uses.
#
# CI runs exactly `ruff check three_fund_rebalance tests`, and pyproject pins
# select = ["E", "F", "I", "UP", "B", "SIM"] with line-length = 100. Catching a
# violation at the edit is cheaper than catching it at review.
#
# Exits 2 with the diagnostics on stderr so Claude sees them and can fix them.
# Any other problem (no ruff, unparseable input, a file outside the two package
# directories) exits 0 and stays out of the way.
set -uo pipefail

root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

file_path=$(python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))
except Exception:
    print("")
' 2>/dev/null) || exit 0

[[ -n "$file_path" && "$file_path" == *.py && -f "$file_path" ]] || exit 0

case "$file_path" in
  "$root"/three_fund_rebalance/*|"$root"/tests/*) ;;
  *) exit 0 ;;
esac

if [[ -x "$root/.venv/bin/ruff" ]]; then
  ruff="$root/.venv/bin/ruff"
elif command -v ruff >/dev/null 2>&1; then
  ruff=ruff
else
  exit 0
fi

output=$("$ruff" check "$file_path" 2>&1) || {
  printf 'ruff check failed on %s:\n\n%s\n' "${file_path#"$root"/}" "$output" >&2
  exit 2
}
exit 0
