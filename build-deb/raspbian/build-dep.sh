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
# <notes-dir>/from-staging, one "package version" per line.
#
# Rebuilding: Raspbian's autobuilders can also lag behind a source upload,
# leaving staging with the new source but only the old armhf binary, which a
# newer package then Breaks (raspbian forky, 2026-09: rust-schemars-derive
# 1.2.2-2's source, but librust-schemars-derive-dev 1.2.2-1, which
# librust-serde-derive-internals-dev 0.30.0-4 Breaks). RASPBIAN_REBUILD names
# such source packages, space-separated (build-deb's raspbian-rebuild input):
# each is fetched from Raspbian's own signed Sources (<codename> and
# <codename>-staging, whichever is newer), built for armhf in this container,
# as Raspbian's builders would, and offered to apt from a local directory
# before the build dependencies are installed. Listed in <notes-dir>/rebuilt.
#
# staging and the rebuilt packages are only ever in this throwaway build
# container. The image, and so the install test that runs in a clean
# container of it, has the suite alone: a package built here whose run-time
# dependencies only staging (or a rebuild) has fails the install test instead
# of being published.
#
# APT_REPO_ACTION_SELFTEST_RASPBIAN_STAGING=always skips the first attempt,
# so the self-test proves the fallback however the archive is today. It is
# read from the environment only: it is not an input.
set -eu
codename=$1
notes=$2
rebuild=${RASPBIAN_REBUILD:-}
mkdir -p "$notes"
: > "$notes/from-staging"
: > "$notes/rebuilt"
keyring=/usr/share/keyrings/apt-repo-action-raspbian.gpg

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
  echo "::warning title=Raspbian staging::the build dependencies can't be installed from raspbian $codename alone (see apt's message above); trying again with $codename-staging, Raspbian's own newer, consistent copy of the same release"
elif [ -z "$codename" ]; then
  echo "::error::APT_REPO_ACTION_SELFTEST_RASPBIAN_STAGING is for Raspbian suites only"
  exit 1
elif [ -n "$rebuild" ]; then
  echo "rebuilding $rebuild from Raspbian's source, with $codename-staging"
else
  echo "self-test: using $codename-staging without trying $codename alone first"
fi

if [ ! -f "$keyring" ]; then
  echo "::error::$keyring is missing from the Raspbian root: can't verify $codename-staging"
  exit 1
fi
# What the suite alone offers, "package version" per line, from the lists apt
# already verified: anything installed later at a version not in here came
# from staging (or from a rebuild).
apt-cache dumpavail | awk '/^Package: /{p=$2} /^Version: /{print p, $2}' | sort -u > "$notes/suite-versions"
archive=http://archive.raspbian.org/raspbian
{
  echo "deb [signed-by=$keyring] $archive $codename-staging main contrib non-free rpi"
  if [ -n "$rebuild" ]; then
    echo "deb-src [signed-by=$keyring] $archive $codename main contrib non-free rpi"
    echo "deb-src [signed-by=$keyring] $archive $codename-staging main contrib non-free rpi"
  fi
} > /etc/apt/sources.list.d/apt-repo-action-raspbian-staging.list
dpkg-query -W -f '${Package} ${Version}\n' | sort -u > "$notes/before"
apt-get update

if [ -n "$rebuild" ]; then
  # Outside the source tree, and not in its parent, so the build's own
  # `cp ../*.deb` never picks these up.
  pool=/var/cache/apt-repo-action-rebuilt
  mkdir -p "$pool"
  for src in $rebuild; do
    work=$(mktemp -d /tmp/rebuild.XXXXXX)
    # apt-get source verifies the .dsc and tarballs against the signed Sources.
    (cd "$work" && apt-get source --only-source "$src")
    dir=$(find "$work" -mindepth 1 -maxdepth 1 -type d | head -n 1)
    if [ -z "$dir" ]; then
      echo "::error::raspbian-rebuild: apt-get source $src unpacked nothing"
      exit 1
    fi
    (cd "$dir" && apt-get build-dep -y ./ && dpkg-buildpackage -us -uc -b)
    cp "$work"/*.deb "$pool"/
    ver=$(cd "$dir" && dpkg-parsechangelog -S Version)
    echo "$src $ver" >> "$notes/rebuilt"
    rm -rf "$work"
  done
  (cd "$pool" && dpkg-scanpackages . > Packages)
  # trusted=yes: these were built here, from sources apt verified, and the
  # directory only exists in this throwaway container.
  echo "deb [trusted=yes] file:$pool ./" > /etc/apt/sources.list.d/apt-repo-action-rebuilt.list
  apt-get update
fi

apt-get build-dep -y ./
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
