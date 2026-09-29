#!/bin/bash
# Install a source tree's build dependencies (apt-get build-dep ./) inside a
# build container. For a Raspbian suite, when the suite alone can't satisfy
# them, fall back to Raspbian's own staging archive, and rebuild what
# Raspbian's builders haven't. Run as root in the build's container, in the
# source tree; build-deb/action.yml's Build step calls it.
#
#   build-dep.sh <raspbian-codename or ""> <notes-dir>
#
# Raspbian builds each release in <codename>-staging and copies it into
# <codename> in batches, and its autobuilders rebuild each source upload for
# armhf in their own time. A testing codename can be half-copied and
# half-built for weeks. On 2026-09-29, raspbian forky:
# - carried 600 packages rebuilt for perl 5.42 while its perl was 5.40
#   (staging had perl 5.42.3 since 2026-09-03), so nothing build-depending on
#   texinfo or a perl XS module could install from forky alone;
# - had, in forky-staging, 920 source packages newer than their armhf binary
#   (91 of them Rust crates), e.g. rust-schemars-derive 1.2.2-2's source but
#   the 1.2.2-1 binary, which librust-serde-derive-internals-dev 0.30.0-4
#   Breaks, and rust-tiff 0.11.3-3's source but the 0.11.3-2 binary, which
#   needs a librust-weezl 0.1 that no longer exists.
#
# So, for a Raspbian suite:
# 1. build-dep from the suite alone; done if that works.
# 2. add <codename>-staging (and both codenames' Sources), verified with the
#    same pinned archive key, at the suite's priority (apt picks one candidate
#    per package by priority, then version: a lower priority would leave the
#    suite's broken candidates in place), and try again.
# 3. while it still fails: ask stale-sources.py which of the packages apt's
#    message names come from a source package the archive has a newer
#    version of than their binary, rebuild those for armhf here from
#    Raspbian's own signed Sources (with the nocheck profile, as their tests
#    aren't what this build is about), offer them to apt from a local
#    directory, and try again. A rebuild's own build dependencies go through
#    the same steps. No stale package to rebuild, or too many rounds, fails.
# RASPBIAN_REBUILD (build-deb's raspbian-rebuild input) names source packages
# to rebuild up front. Everything taken from staging or rebuilt is listed in
# <notes-dir>/from-staging and <notes-dir>/rebuilt.
#
# staging and the rebuilt packages are only ever installed in this throwaway
# build container, never in the image. What the build compiles from them can
# still end up in its packages (statically linked Rust crates, headers): it is
# Raspbian's own code, and each is listed in the notes. The image, and so the
# install test that runs in a clean container of it, has the suite alone: a
# package whose run-time dependencies only staging (or a rebuild) has fails
# the install test instead of being published. That covers run-time
# dependencies only, and only where an install test runs.
#
# APT_REPO_ACTION_SELFTEST_RASPBIAN_STAGING=always skips step 1, so the
# self-test proves the fallback however the archive is today. It is read from
# the environment only: it is not an input.
set -eu
codename=$1
notes=$2
rebuild=${RASPBIAN_REBUILD:-}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$notes"
: > "$notes/from-staging"
: > "$notes/rebuilt"
keyring=/usr/share/keyrings/apt-repo-action-raspbian.gpg
pool=/var/cache/apt-repo-action-rebuilt
max_rounds=25
max_depth=6

if [ -n "$rebuild" ] && [ -z "$codename" ]; then
  echo "::error::raspbian-rebuild is for raspbian-<codename> suites only"
  exit 1
fi
for src in $rebuild; do
  case "$src" in
    *[!a-z0-9.+-]*|[!a-z0-9]*) echo "::error::raspbian-rebuild: \"$src\" is not a Debian source package name"; exit 1 ;;
  esac
done

if [ -z "$rebuild" ] && [ "${APT_REPO_ACTION_SELFTEST_RASPBIAN_STAGING:-}" != always ]; then
  if apt-get build-dep -y ./; then
    exit 0
  fi
  if [ -z "$codename" ]; then
    echo "::error::apt-get build-dep failed: the build dependencies in debian/control can't be installed from the suite (see apt's message above)"
    exit 1
  fi
  echo "::warning title=Raspbian staging::the build dependencies can't be installed from raspbian $codename alone (see apt's message above); trying again with $codename-staging, Raspbian's own newer copy of the same release, and rebuilding what Raspbian hasn't"
elif [ -z "$codename" ]; then
  echo "::error::APT_REPO_ACTION_SELFTEST_RASPBIAN_STAGING is for Raspbian suites only"
  exit 1
fi

if [ ! -f "$keyring" ]; then
  echo "::error::$keyring is missing from the Raspbian root: can't verify $codename-staging"
  exit 1
fi
# What the suite alone offers, "package version" per line, from the lists apt
# already verified: anything installed later at a version not in here came
# from staging or a rebuild.
apt-cache dumpavail | awk '/^Package: /{p=$2} /^Version: /{print p, $2}' | sort -u > "$notes/suite-versions"
archive=http://archive.raspbian.org/raspbian
{
  echo "deb [signed-by=$keyring] $archive $codename-staging main contrib non-free rpi"
  echo "deb-src [signed-by=$keyring] $archive $codename main contrib non-free rpi"
  echo "deb-src [signed-by=$keyring] $archive $codename-staging main contrib non-free rpi"
} > /etc/apt/sources.list.d/apt-repo-action-raspbian-staging.list
dpkg-query -W -f '${Package} ${Version}\n' | sort -u > "$notes/before"
apt-get update

refresh_pool() {
  (cd "$pool" && dpkg-scanpackages . > Packages) || return 1
  # trusted=yes: built here, from sources apt verified, and the directory only
  # exists in this throwaway container.
  echo "deb [trusted=yes] file:$pool ./" > /etc/apt/sources.list.d/apt-repo-action-rebuilt.list || return 1
  apt-get update || return 1
}

rounds=0
# build_deps <dir> <depth> [apt-get build-dep options]: install <dir>'s build
# dependencies, rebuilding stale Raspbian sources until they install.
build_deps() {
  local dir=$1 depth=$2 log stale src
  shift 2
  log=$(mktemp)
  while :; do
    if (cd "$dir" && apt-get build-dep -y "$@" ./) > "$log" 2>&1; then
      cat "$log"; rm -f "$log"; return 0
    fi
    cat "$log"
    stale=$(python3 "$here/stale-sources.py" < "$log" | while read -r s; do
              grep -q "^$s " "$notes/rebuilt" || echo "$s"; done)
    if [ -z "$stale" ]; then
      echo "::error::the build dependencies can't be installed from raspbian $codename, $codename-staging or anything rebuilt, and apt's message names no package whose Raspbian source is newer than its binary (see apt's message above)"
      rm -f "$log"; return 1
    fi
    rounds=$((rounds + 1))
    if [ "$rounds" -gt "$max_rounds" ]; then
      echo "::error::gave up after $max_rounds rounds of rebuilding stale Raspbian sources; still needed: $stale"
      rm -f "$log"; return 1
    fi
    for src in $stale; do
      rebuild_source "$src" "$((depth + 1))" || { rm -f "$log"; return 1; }
    done
  done
}

# rebuild_source <source package> <depth>: build it for armhf from Raspbian's
# signed Sources, as Raspbian's builders would, and offer it to apt.
rebuild_source() {
  local src=$1 depth=$2 work dir ver
  if [ "$depth" -gt "$max_depth" ]; then
    echo "::error::rebuilding $src would go $depth levels deep (more than $max_depth)"
    return 1
  fi
  echo "::group::rebuilding $src (level $depth) from Raspbian's source"
  work=$(mktemp -d /tmp/rebuild.XXXXXX)
  # apt-get source verifies the .dsc and tarballs against the signed Sources.
  # (Called from `... || return 1`, where bash ignores set -e: every step
  # that can fail says so itself.)
  (cd "$work" && apt-get source --only-source "$src") || return 1
  dir=$(find "$work" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [ -z "$dir" ]; then
    echo "::error::apt-get source $src unpacked nothing"
    return 1
  fi
  build_deps "$dir" "$depth" --build-profiles=nocheck || return 1
  (cd "$dir" && DEB_BUILD_OPTIONS=nocheck DEB_BUILD_PROFILES=nocheck dpkg-buildpackage -us -uc -b) || {
    echo "::error::rebuilding $src failed (see its build log above)"; return 1; }
  mkdir -p "$pool" || return 1
  cp "$work"/*.deb "$pool"/ || return 1
  ver=$(cd "$dir" && dpkg-parsechangelog -S Version) || return 1
  echo "$src $ver" >> "$notes/rebuilt"
  rm -rf "$work"
  refresh_pool || return 1
  echo "::endgroup::"
}

for src in $rebuild; do
  rebuild_source "$src" 1 || exit 1
done
build_deps . 0 || exit 1

dpkg-query -W -f '${Package} ${Version}\n' | sort -u > "$notes/after"
# Installed or upgraded by this attempt, and at a version the suite lacks.
comm -13 "$notes/before" "$notes/after" | comm -23 - "$notes/suite-versions" > "$notes/from-staging"
rm -f "$notes/before" "$notes/after" "$notes/suite-versions"
n=$(grep -c . "$notes/from-staging" || true)
echo "from $codename-staging or rebuilt ($n packages):"
cat "$notes/from-staging"
if [ -s "$notes/rebuilt" ]; then
  echo "rebuilt from Raspbian's source:"
  cat "$notes/rebuilt"
fi
