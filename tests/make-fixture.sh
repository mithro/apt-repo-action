#!/bin/bash
# Build the self-test's inputs: one throwaway Architecture: all package in
# <apt-root>/<suite>/ for each suite, and a throwaway signing key.
#
# Usage: tests/make-fixture.sh <apt-root> "<suite> ..." <private-key-out>
set -euo pipefail

root=$1
suites=$2
key_out=$3

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

pkg=$work/pkg
mkdir -p "$pkg/DEBIAN" "$pkg/usr/share/apt-repo-selftest"
echo "apt-repo-action self-test fixture" > "$pkg/usr/share/apt-repo-selftest/README"
cat > "$pkg/DEBIAN/control" <<'EOF'
Package: apt-repo-selftest
Version: 1.0
Architecture: all
Maintainer: apt-repo-action self-test <selftest@invalid>
Description: apt-repo-action self-test fixture
 Installed by the self-test to prove the published repository is usable.
EOF

for suite in $suites; do
  mkdir -p "$root/$suite"
  dpkg-deb --build --root-owner-group "$pkg" "$root/$suite/apt-repo-selftest_1.0_all.deb"
done

# RSA 4096, like the real repositories' keys.
export GNUPGHOME=$work/gnupg
mkdir -m 700 "$GNUPGHOME"
gpg --batch --passphrase '' --quick-gen-key \
  'apt-repo-action self-test <selftest@invalid>' rsa4096 sign never
gpg --batch --armor --export-secret-keys > "$key_out"
