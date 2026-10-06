#!/bin/bash
# Pre-commit checks for vkb-hotas. Read-only; exits non-zero if something must be fixed first.
# Usage: precheck.sh [REPO_DIR]
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=${1:-$(cd "$here/../../../.." && pwd)}
cd "$repo" || exit 2
export PYTHONDONTWRITEBYTECODE=1
fail=0

echo "== syntax"
for f in */*.py .claude/skills/*/scripts/*.py; do
    [[ -e $f ]] || continue
    python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" || { echo "  FAIL $f"; fail=1; }
done
for f in *.sh .claude/skills/*/scripts/*.sh; do
    [[ -e $f ]] || continue
    bash -n "$f" || { echo "  FAIL $f"; fail=1; }
done
t=$(mktemp --suffix=.rules); cp service/72-vkb-hotas.rules "$t"   # verify wants a real *.rules file
udevadm verify "$t" >/dev/null 2>&1 || { echo "  FAIL udev rule"; fail=1; }; rm -f "$t"
(( fail )) || echo "  ok"

echo "== privacy (tracked + new untracked files)"
if git grep --untracked -n -I -E '/home/[a-z]|gh[opsu]_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY' -- . ':!.claude/skills/ship-change/scripts/precheck.sh'; then
    echo "  FAIL: personal paths or secrets above"; fail=1
else
    echo "  ok"
fi

echo "== API_VERSION"
api_head=$( (git show HEAD:mapper/vkb-mapper.py 2>/dev/null || git show HEAD:vkb-mapper.py 2>/dev/null) | grep -oP '^API_VERSION = \K[0-9]+')
api_now=$(grep -oP '^API_VERSION = \K[0-9]+' mapper/vkb-mapper.py)
page=$(grep -oP 'l\.api !== \K[0-9]+' mapper/vkb-mapper.html)
echo "  HEAD=${api_head:-?} now=$api_now page expects=$page"
[[ $api_now == "$page" ]] || { echo "  FAIL: server and page disagree"; fail=1; }
if [[ -n $api_head && $api_now == "$api_head" ]] && ! git diff --quiet HEAD -- mapper/; then
    echo "  note: mapper changed but API_VERSION didn't. Fine unless the page now relies on new server behaviour/format."
fi

echo "== changes"
git status --short
git diff --stat HEAD | tail -1
for f in README.md CLAUDE.md; do git diff --quiet HEAD -- "$f" && echo "  $f unchanged: confirm no doc update is needed"; done
exit $fail
