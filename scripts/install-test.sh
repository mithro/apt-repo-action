#!/bin/sh
# The install test of the reusable build-deb.yml (docs/packaging.md,
# "Builds"). Runs as root inside a clean container of the suite, with:
#   /debs          the packages to install;
#   /bundled       the packages bundled from dependency repositories declared
#                  with `bundle`, which the published repository serves, with
#                  their Packages index (bundle-depends.py fetch --index; may
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
# The bundled packages as a local source, so apt chooses among them (one of
# several alternatives, one version of several) as it would from the
# published repository, instead of installing them all. trusted=yes is only
# for this throwaway container: bundle-depends.py verified every file
# against its repository's signed index, and its control file against that.
if [ -s /bundled/Packages ]; then
  echo "deb [trusted=yes] file:/bundled ./" > /etc/apt/sources.list.d/bundled.list
fi
apt-get update
apt-get install -y /debs/*.deb
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
