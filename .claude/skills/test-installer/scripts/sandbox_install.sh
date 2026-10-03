#!/bin/bash
# Run install.sh (then uninstall.sh) without root, against a throwaway root directory.
# System paths are redirected into the sandbox and systemctl/udevadm/modprobe are stubbed,
# so nothing on the real system is touched. Verifies every installed file against its source.
#
#   sandbox_install.sh [--keep] [install.sh args...]     default args: --device 231d:3200 --user $USER
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../../.." && pwd)
keep=0; [[ ${1:-} == --keep ]] && { keep=1; shift; }
args=("$@"); [[ ${#args[@]} -eq 0 ]] && args=(--device 231d:3200 --user "$USER")
export PYTHONDONTWRITEBYTECODE=1

R=$(mktemp -d)
cleanup() { (( keep )) && echo "sandbox kept: $R" || rm -rf "$R"; rm -f "$repo/.sandbox-install.sh" "$repo/.sandbox-uninstall.sh"; }
trap cleanup EXIT
# directories that always exist on a real systemd system
mkdir -p "$R"/{shims,etc/modules-load.d,etc/udev/rules.d,etc/systemd/system,etc/default,usr/local/bin,usr/local/share/applications,run/vkb-hotas}
for c in systemctl udevadm modprobe; do
    printf '#!/bin/sh\necho "[shim] %s $*" >> "%s/shims.log"\n' "$c" "$R" > "$R/shims/$c"; chmod +x "$R/shims/$c"
done
sandbox() {  # rewrite a repo script so it runs inside $R; keep it in the repo dir so $here resolves
    sed -e "s#/usr/local#$R/usr/local#g" -e "s#/etc/#$R/etc/#g" -e "s#/run/vkb-hotas#$R/run/vkb-hotas#g" \
        -e 's#\[\[ \$EUID -eq 0 \]\] ||#true ||#' "$repo/$1" > "$repo/$2"
}
fail=0

echo "== install.sh ${args[*]}"
sandbox install.sh .sandbox-install.sh
if PATH="$R/shims:$PATH" bash "$repo/.sandbox-install.sh" "${args[@]}" > "$R/install.out" 2>&1; then
    echo "  exit 0"
else
    echo "  FAILED (exit $?):"; sed 's/^/    /' "$R/install.out"; exit 1
fi

echo "== installed files"
(cd "$R" && find usr etc \( -type f -o -type l \) | sort | sed 's/^/  /')

echo "== each installed file identical to its repo source"
while read -r f; do
    name=$(basename "$f")
    src=$(cd "$repo" && git ls-files --cached --others --exclude-standard | grep -E "(^|/)$name$" | head -1)
    if [[ -z $src ]]; then echo "  (no source) $f"; continue; fi
    if cmp -s "$repo/$src" "$R/$f"; then echo "  ok        $src -> /$f"; else echo "  MISMATCH  $src -> /$f"; fail=1; fi
done < <(cd "$R" && find usr/local/lib etc/systemd usr/local/share -type f | sort)

echo "== generated files"
rules=$R/etc/udev/rules.d/72-vkb-hotas.rules
if [[ -f $rules ]] && ! grep -q '@VID@\|@PID@' "$rules"; then echo "  ok        udev rule rendered ($(grep -c 'ATTRS{idVendor}' "$rules") device lines)"; else echo "  FAIL      udev rule missing or unrendered"; fail=1; fi
udevadm verify "$rules" >/dev/null 2>&1 && echo "  ok        udev rule verifies" || { echo "  FAIL      udevadm verify"; fail=1; }
grep -E '^VKB_HOTAS_(DEVICE|USER)=' "$R/etc/default/vkb-hotas" | sed 's/^/  config    /'
link=$R/usr/local/bin/vkb-mapper
if [[ -L $link ]] && python3 "$link" --help >/dev/null 2>&1; then echo "  ok        launcher symlink runs"; else echo "  FAIL      launcher"; fail=1; fi
if python3 "$R/usr/local/lib/vkb-hotas/vkb-hotas.py" --help >/dev/null 2>&1; then echo "  ok        installed daemon imports (flat layout)"; else echo "  FAIL      installed daemon"; fail=1; fi
echo "  shim calls: $(cut -d' ' -f2 "$R/shims.log" | sort | uniq -c | tr '\n' ' ')"

echo "== uninstall.sh"
sandbox uninstall.sh .sandbox-uninstall.sh
PATH="$R/shims:$PATH" bash "$repo/.sandbox-uninstall.sh" > "$R/uninstall.out" 2>&1 || { echo "  FAILED:"; sed 's/^/    /' "$R/uninstall.out"; fail=1; }
left=$(cd "$R" && find usr etc \( -type f -o -type l \) ! -path 'etc/default/vkb-hotas' | sort)
if [[ -z $left ]]; then echo "  ok        everything removed (config in etc/default kept, as intended)"; else echo "  LEFT BEHIND:"; echo "$left" | sed 's/^/    /'; fail=1; fi

(( fail )) && echo "RESULT: FAIL" || echo "RESULT: OK"
exit $fail
