#!/bin/sh
# Install a source tree's build dependencies (apt-get build-dep ./) inside a
# build container, falling back to Raspbian's <codename>-staging when the
# suite alone can't satisfy them. Run as root in the build's container, in
# the source tree; build-deb/action.yml's Build step calls it.
#
#   build-dep.sh <raspbian-codename or ""> <notes-dir>
#
# Raspbian builds each release in <codename>-staging and copies it into
# <codename> in batches. A testing codename can be left half-copied for weeks:
# in 2026-09 raspbian forky carried 600 packages rebuilt for perl 5.42 while
# its perl was still 5.40 (staging had 5.42.3 since 2026-09-03), so anything
# build-depending on texinfo or a perl XS module couldn't be installed from
# forky alone. staging is the consistent state of the same release, signed
# with the same archive key.
#
# So: build-dep from the suite first. Only if that fails, and only for a
# Raspbian suite, add <codename>-staging, verified with the same pinned key,
# and try once more. staging gets the same priority as the suite (apt picks
# one candidate per package by priority, then by version; with a lower
# priority apt would still pick the suite's broken candidate, and nothing
# would change). The packages that came from staging are written to
# <notes-dir>/from-staging, one "package version" per line, for the step
# summary.
#
# staging is only ever added to this throwaway build container. The image,
# and so the install test that runs in a clean container of it, has the
# suite alone: a package built here whose run-time dependencies only staging
# has (a shlibs version raised by a library that came from staging, say)
# fails the install test instead of being published.
#
# APT_REPO_ACTION_SELFTEST_RASPBIAN_STAGING=always skips the first attempt,
# so the self-test proves the fallback however the archive is today. It is
# read from the environment only: it is not an input.
set -eu
codename=$1
notes=$2
mkdir -p "$notes"
: > "$notes/from-staging"

if [ "${APT_REPO_ACTION_SELFTEST_RASPBIAN_STAGING:-}" != always ]; then
  if apt-get build-dep -y ./; then
    exit 0
  fi
  if [ -z "$codename" ]; then
    echo "::error::apt-get build-dep failed: the build dependencies in debian/control can't be installed from the suite (see apt's message above)"
    exit 1
  fi
  echo "::warning title=Raspbian staging::the build dependencies can't be installed from raspbian $codename alone (see apt's message above); trying again with $codename-staging, Raspbian's own newer, consistent copy of the same release"
elif [ -z "$codename" ]; then
  echo "::error::APT_REPO_ACTION_SELFTEST_RASPBIAN_STAGING is for Raspbian suites only"
  exit 1
else
  echo "self-test: using $codename-staging without trying $codename alone first"
fi

keyring=/usr/share/keyrings/apt-repo-action-raspbian.gpg
if [ ! -f "$keyring" ]; then
  echo "::error::$keyring is missing from the Raspbian root: can't verify $codename-staging"
  exit 1
fi
# What the suite alone offers, "package version" per line, from the lists apt
# already verified: anything installed later at a version not in here came
# from staging.
apt-cache dumpavail | awk '/^Package: /{p=$2} /^Version: /{print p, $2}' | sort -u > "$notes/suite-versions"
echo "deb [signed-by=$keyring] http://archive.raspbian.org/raspbian $codename-staging main contrib non-free rpi" \
  > /etc/apt/sources.list.d/apt-repo-action-raspbian-staging.list
dpkg-query -W -f '${Package} ${Version}\n' | sort -u > "$notes/before"
apt-get update
apt-get build-dep -y ./
dpkg-query -W -f '${Package} ${Version}\n' | sort -u > "$notes/after"
# Installed or upgraded by this attempt, and at a version the suite lacks.
comm -13 "$notes/before" "$notes/after" | comm -23 - "$notes/suite-versions" > "$notes/from-staging"
rm -f "$notes/before" "$notes/after" "$notes/suite-versions"
n=$(grep -c . "$notes/from-staging" || true)
echo "from $codename-staging ($n packages):"
cat "$notes/from-staging"
