#!/bin/sh
# Raspbian's <codename>-staging as a fallback source, inside a container of
# build-deb's Raspbian root (docs/packaging.md, "Builds"; build-dep.sh says
# why a testing codename can need it). Run as root.
#
#   staging.sh add <codename> <notes-dir> [--sources]
#     Records what the suite alone offers, then adds <codename>-staging,
#     verified with the root's pinned Raspbian archive key, at the suite's
#     priority, and runs apt-get update. --sources also adds both codenames'
#     deb-src (build-dep.sh rebuilds from them).
#   staging.sh report <notes-dir>
#     Writes <notes-dir>/from-staging: every package installed or upgraded
#     since `add`, at a version the suite alone doesn't have, and prints it.
#
# Only for throwaway containers: the Raspbian image itself never has staging.
# What a repository publishes must install from the suite alone (build-deb's
# install test proves that); staging is for build dependencies and for a
# test harness's own tools, never for our packages or their dependencies.
set -eu
keyring=/usr/share/keyrings/apt-repo-action-raspbian.gpg
archive=http://archive.raspbian.org/raspbian
cmd=${1:?usage: staging.sh add <codename> <notes-dir> [--sources] | report <notes-dir>}

case "$cmd" in
  add)
    codename=${2:?codename}
    notes=${3:?notes-dir}
    case "$codename" in
      ""|*[!a-z]*) echo "::error::staging.sh: \"$codename\" is not a Raspbian codename"; exit 1 ;;
    esac
    if [ ! -f "$keyring" ]; then
      echo "::error::$keyring is missing: this is not build-deb's Raspbian root, so $codename-staging can't be verified"
      exit 1
    fi
    mkdir -p "$notes"
    # What the suite alone offers, "package version" per line, from the lists
    # apt already verified: anything installed later at a version not in here
    # came from staging (or a local rebuild).
    apt-cache dumpavail | awk '/^Package: /{p=$2} /^Version: /{print p, $2}' | sort -u > "$notes/suite-versions"
    dpkg-query -W -f '${Package} ${Version}\n' | sort -u > "$notes/before"
    {
      echo "deb [signed-by=$keyring] $archive $codename-staging main contrib non-free rpi"
      if [ "${4:-}" = --sources ]; then
        echo "deb-src [signed-by=$keyring] $archive $codename main contrib non-free rpi"
        echo "deb-src [signed-by=$keyring] $archive $codename-staging main contrib non-free rpi"
      fi
    } > /etc/apt/sources.list.d/apt-repo-action-raspbian-staging.list
    apt-get update
    ;;
  report)
    notes=${2:?notes-dir}
    if [ ! -f "$notes/before" ] || [ ! -f "$notes/suite-versions" ]; then
      echo "::error::staging.sh report: no \`staging.sh add\` notes in $notes"
      exit 1
    fi
    dpkg-query -W -f '${Package} ${Version}\n' | sort -u > "$notes/after"
    # Installed or upgraded since `add`, and at a version the suite lacks.
    comm -13 "$notes/before" "$notes/after" | comm -23 - "$notes/suite-versions" > "$notes/from-staging"
    rm -f "$notes/before" "$notes/after" "$notes/suite-versions"
    echo "from staging or rebuilt ($(grep -c . "$notes/from-staging" || true) packages):"
    cat "$notes/from-staging"
    ;;
  *)
    echo "::error::staging.sh: unknown command $cmd"
    exit 1
    ;;
esac
