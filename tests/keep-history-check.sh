#!/bin/bash
# Serve a published fixture repository (version 1.0 in bookworm/) as the "live
# site" and check scripts/keep-history.py against it.
#
# Usage: tests/keep-history-check.sh <published-apt-root>
set -euo pipefail

live=$(realpath "$1")
scripts=$(realpath "$(dirname "$0")/../scripts")
work=$(mktemp -d)
python3 -m http.server -d "$live" 8766 > "$work/http.log" 2>&1 &
server=$!
trap 'kill $server; rm -rf "$work"' EXIT
site=http://127.0.0.1:8766
for _ in $(seq 50); do curl -fs "$site/bookworm/Packages" > /dev/null && break; sleep 0.2; done

old=$live/bookworm/apt-repo-selftest_1.0_all.deb

# A new build of the fixture at another version, alone in <tree>/bookworm/.
build() {  # build <tree> <version>
  rm -rf "$work/pkg" "$1"
  dpkg-deb -R "$old" "$work/pkg"
  sed -i "s/^Version: .*/Version: $2/" "$work/pkg/DEBIAN/control"
  mkdir -p "$1/bookworm"
  dpkg-deb --build --root-owner-group "$work/pkg" "$1/bookworm/apt-repo-selftest_$2_all.deb" > /dev/null
}
keep() {  # keep <tree> <limit-bytes>
  python3 "$scripts/keep-history.py" --site "$site" --dest "$1" --suites bookworm \
    --limit-bytes "$2"
}
fail() { echo "::error::$*"; exit 1; }

# An upgrade keeps the previous version, byte for byte, and both are indexed.
build "$work/up" 2.0
keep "$work/up" 100000000
cmp "$old" "$work/up/bookworm/apt-repo-selftest_1.0_all.deb" || fail "1.0 was not kept on upgrade"
(cd "$work/up/bookworm" && dpkg-scanpackages --multiversion .) > "$work/Packages"
grep -q '^Version: 1.0$' "$work/Packages" || fail "the kept 1.0 is not indexed"
grep -q '^Version: 2.0$' "$work/Packages" || fail "the new 2.0 is not indexed"

# A deliberate downgrade keeps nothing higher than itself.
build "$work/down" 0.9
keep "$work/down" 100000000
[ ! -e "$work/down/bookworm/apt-repo-selftest_1.0_all.deb" ] || fail "1.0 was kept over a downgrade to 0.9"

# Nothing is kept when it would take the deploy over the limit: one byte short
# of room for 1.0.
build "$work/tight" 2.0
keep "$work/tight" $(( $(du -sb "$work/tight" | cut -f1) + $(stat -c %s "$old") - 1 ))
[ ! -e "$work/tight/bookworm/apt-repo-selftest_1.0_all.deb" ] || fail "1.0 was kept past the size limit"

# carry-over.py --measure counts a repository's bytes and writes nothing.
mkdir "$work/measured"
bytes=$(python3 "$scripts/carry-over.py" --measure --site "$site" --dest "$work/measured" bookworm)
[ "$bytes" -gt "$(stat -c %s "$old")" ] || fail "--measure reported $bytes bytes"
[ -z "$(ls -A "$work/measured")" ] || fail "--measure wrote files"

echo "keep-history checks passed"
