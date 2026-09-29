#!/bin/sh
# For an end-to-end test that installs tools of its own next to our packages
# (docs/packaging.md, "Builds"): prove the tools changed nothing our packages
# run with. Run as root in the test's container.
#
#   install-guard.sh save <file>    after installing ours (from the suite
#                                   alone): every installed package and
#                                   version, ours and their whole closure
#   install-guard.sh check <file>   after installing the tools: fails, and
#                                   lists them, if any package in <file> has
#                                   a different version or is gone. Packages
#                                   the tools added are allowed.
#
# Upgrade the container from the suite first (apt-get dist-upgrade): an image
# can lag its archive, and the tools would otherwise upgrade such packages to
# the suite's own newer versions, which isn't what this is looking for.
set -eu
cmd=${1:?usage: install-guard.sh save|check <file>}
file=${2:?usage: install-guard.sh save|check <file>}

case "$cmd" in
  save)
    mkdir -p "$(dirname "$file")"
    dpkg-query -W -f '${Package} ${Version}\n' | sort > "$file"
    echo "install-guard: $(grep -c . "$file") packages installed"
    ;;
  check)
    if [ ! -f "$file" ]; then
      echo "::error::install-guard.sh check: no saved list $file (run install-guard.sh save first)"
      exit 1
    fi
    now=$(mktemp)
    dpkg-query -W -f '${Package} ${Version}\n' | sort > "$now"
    changed=$(join "$file" "$now" | awk '$2 != $3 {print $1 ": " $2 " -> " $3}')
    gone=$(join -v 1 "$file" "$now" | awk '{print $1 " " $2 " (removed)"}')
    rm -f "$now"
    if [ -n "$changed$gone" ]; then
      echo "::error::installing the test's tools changed what our packages run with"
      printf '%s\n' "$changed" "$gone" | grep .
      exit 1
    fi
    echo "install-guard: nothing our packages run with changed"
    ;;
  *)
    echo "::error::install-guard.sh: unknown command $cmd"
    exit 1
    ;;
esac
