#!/bin/sh
# Where apt took the dependency from, and which ARM architecture its binary
# was built for: 6 in a raspbian-<codename> suite (docs/packaging.md, "Suites").
set -eu
apt-cache policy apt-repo-selftest-hello
apt-repo-selftest-bundle --cpu-arch
