#!/usr/bin/env bash
# Health check for fault-injection-lab bootstrap
# Exit codes: 0=ok, 1=degraded, 2=broken

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

errors=0
warnings=0
checks=()

check_hard() {
    local name="$1" cmd="$2"
    if eval "$cmd" >/dev/null 2>&1; then
        checks+=("{\"name\":\"$name\",\"status\":\"ok\"}")
    else
        checks+=("{\"name\":\"$name\",\"status\":\"FAIL\"}")
        ((errors++)) || true
    fi
}

check_soft() {
    local name="$1" cmd="$2"
    if eval "$cmd" >/dev/null 2>&1; then
        checks+=("{\"name\":\"$name\",\"status\":\"ok\"}")
    else
        checks+=("{\"name\":\"$name\",\"status\":\"warn\"}")
        ((warnings++)) || true
    fi
}

# Hard requirements
check_hard "git_repo"       "test -d .git"
check_hard "git_remote"     "git remote get-url origin"
check_hard "spec_file"      "test -f spec.org"
check_hard "claude_md"      "test -f CLAUDE.md"
check_hard "agents_md"      "test -f AGENTS.md"
check_hard "cprr_store"     "test -f .cprr/conjectures.json"
check_hard "cprr_has_open"  "cprr list 2>/dev/null | grep -q ."

# Soft requirements
check_soft "bd_server"      "bd ready --json 2>/dev/null"
check_soft "bd_has_ready"   "bd ready 2>/dev/null | grep -q ."
check_soft "sb_doctor"      "sb doctor 2>/dev/null"

# Output JSON
items=$(IFS=,; echo "${checks[*]}")

if [ "$errors" -gt 0 ]; then
    status="broken"; exit_code=2
elif [ "$warnings" -gt 0 ]; then
    status="degraded"; exit_code=1
else
    status="ok"; exit_code=0
fi

cat <<EOF
{
  "status": "$status",
  "errors": $errors,
  "warnings": $warnings,
  "checks": [$items]
}
EOF

exit "$exit_code"
