#!/bin/bash
# Read-only post-install check for vkb-hotas. Prints a report; changes nothing.
# Usage: verify_install.sh [REPO_DIR]   (default: the repo this script lives in)
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=${1:-$(cd "$here/../../../.." && pwd)}
lib=/usr/local/lib/vkb-hotas
cfg=${XDG_CONFIG_HOME:-$HOME/.config}/vkb-hotas
export PYTHONDONTWRITEBYTECODE=1

echo "== installed copy vs repo ($repo)"
if [[ ! -d $lib ]]; then
    echo "  NOT INSTALLED ($lib missing): user must run: sudo $repo/install.sh"
    exit 0
fi
# repo source -> flat installed name (the install layout is flat in /usr/local/lib/vkb-hotas)
for src in service/vkb-hotas.py common/vkb_common.py mapper/vkb-mapper.py mapper/vkb-mapper.html README.md; do
    f=$(basename "$src")
    if cmp -s "$repo/$src" "$lib/$f"; then echo "  same     $src"; else echo "  DIFFERS  $src"; fi
done
inst_epoch=$(stat -c %Y "$lib/vkb-mapper.py")
echo "  installed at: $(date -d @"$inst_epoch" '+%F %T')"
api_repo=$(grep -oP '^API_VERSION = \K[0-9]+' "$repo/mapper/vkb-mapper.py" 2>/dev/null)
api_inst=$(grep -oP '^API_VERSION = \K[0-9]+' "$lib/vkb-mapper.py" 2>/dev/null)
echo "  API_VERSION: repo=${api_repo:-?} installed=${api_inst:-?}"

echo "== running HOTAS Mapper processes"
found=0
for p in $(pgrep -x python3); do
    c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null) || continue
    case "$c" in *vkb-mapper*)
        found=1
        start=$(stat -c %Y /proc/$p)
        if (( start < inst_epoch )); then state="STALE (started before install: user must close it and reopen)"; else state="current"; fi
        echo "  pid $p started $(date -d @"$start" '+%F %T'): $state :: $c";;
    esac
done
(( found )) || echo "  none (next launch will be the installed version)"

echo "== health (vkb-check host section)"
"$repo/tools/vkb-check.py" 2>&1 | sed -n '/^== host/,/^== Proton log/p' | grep -v '^== Proton log'

echo "== user's files (validated read-only with the INSTALLED code)"
python3 - "$lib" "$cfg" <<'PY'
import importlib.util, json, os, sys
lib, cfg = sys.argv[1], sys.argv[2]
sys.path.insert(0, lib)
spec = importlib.util.spec_from_file_location("m", os.path.join(lib, "vkb-mapper.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import vkb_common as c
mp, lp = os.path.join(cfg, "mapping.json"), os.path.join(cfg, "layout.json")
if os.path.exists(mp):
    try:
        mm = c.load_mapping(mp)
        print(f"  mapping.json OK: profiles {list(mm['profiles'])}, active {mm['active']!r}")
    except Exception as e:
        print(f"  mapping.json INVALID: {e}")
else:
    print("  mapping.json: none yet (built-in default profile)")
if os.path.exists(lp):
    try:
        raw = json.load(open(lp))
        lay = m.normalize_layout(raw)
        conv = " (old format, converted on load)" if raw.get("version", 1) < lay.get("version", 1) else ""
        print(f"  layout.json OK{conv}: version {lay.get('version')}")
        for v in lay["views"]:
            pk = m.picture_key(v) if hasattr(m, "picture_key") else v["id"]
            pins = lay.get("pins", {}).get(pk, {})
            hats_here = [k for k in pins if k.startswith("c:")]
            print(f"    view {v['name']!r}: {pk}, {len(pins) - len(hats_here)} pins, {len(hats_here)} hats placed")
        if "hats" in lay:
            print(f"    hats defined: {[h['name'] for h in lay['hats']]}")
        print(f"    button names: {lay.get('names') or 'none'}")
    except Exception as e:
        print(f"  layout.json INVALID for the installed code: {e}")
else:
    print("  layout.json: none yet (defaults)")
PY
