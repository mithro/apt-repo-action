#!/bin/sh
# The install test of the reusable build-deb.yml (docs/packaging.md,
# "Builds"). Runs as root inside a clean container of the suite, with:
#   /debs          the packages to install;
#   /bundled       the packages bundled from dependency repositories declared
#                  with `bundle`, which the published repository serves (may
#                  be empty or absent);
#   /apt-sources   the suite's other dependency repositories, as
#                  scripts/apt-sources.py writes them (its install.sh does
#                  nothing without any);
#   /src           the source tree, read-only.
# Installs every package with its dependencies, then runs the repository's
# packaging/install-test.sh if it has one, or else `<command> --version` for
# each command the packages ship.
set -eu
export DEBIAN_FRONTEND=noninteractive
sh /apt-sources/install.sh
apt-get update
bundled=
for deb in /bundled/*.deb; do
  if [ -f "$deb" ]; then bundled="$bundled $deb"; fi
done
# shellcheck disable=SC2086 # a list of paths without spaces
apt-get install -y /debs/*.deb $bundled
if [ -f /src/packaging/install-test.sh ]; then
  echo "running packaging/install-test.sh"
  cd /src
  exec sh packaging/install-test.sh
fi
n=0
for deb in /debs/*.deb; do
  pkg=$(dpkg-deb -f "$deb" Package)
  for f in $(dpkg -L "$pkg" | grep -E '^(/usr)?/s?bin/[^/]+$' || true); do
    if [ -f "$f" ] && [ -x "$f" ]; then
      echo "+ $f --version"
      "$f" --version
      n=$((n + 1))
    fi
  done
done
echo "install test: every package installed; $n commands answered --version"
