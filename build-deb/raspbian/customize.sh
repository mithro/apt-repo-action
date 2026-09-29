#!/bin/sh
# mmdebstrap --customize-hook for the raspbian-<codename> build roots: runs
# once the packages are installed, with the root's path as $1.
#
# Raspbian's archive key (raspbian.public.key) is bound to its signing key
# with SHA-1 self-signatures only. apt 3 (trixie on) verifies with Sequoia's
# sqv, whose default policy (/usr/share/apt/default-sequoia.config) stops
# accepting SHA-1 for second-preimage resistance on 2026-02-01, and so rejects
# Raspbian's own archive: "Signing key ... is not bound ... SHA1 is not
# considered secure since 2026-02-01". That file says to override it by
# copying it to /etc/crypto-policies/back-ends/apt-sequoia.config, which
# replaces it. The copy changes that one date and nothing else; it only
# exists inside these build roots. apt can't scope a policy to one source,
# so it covers every source in the root, and what it widens is only keys
# bound with SHA-1 self-signatures: README.md, "What the override covers".
set -eu
root=$1
default=$root/usr/share/apt/default-sequoia.config
[ -f "$default" ] || exit 0    # apt 2 (bookworm) verifies with gpgv instead
mkdir -p "$root/etc/crypto-policies/back-ends"
override=$root/etc/crypto-policies/back-ends/apt-sequoia.config
sed 's/^sha1\.second_preimage_resistance *=.*/sha1.second_preimage_resistance = 2030-02-01    # apt-repo-action: Raspbian'"'"'s archive key is bound with SHA-1 only (build-deb\/raspbian\/README.md)/' \
  "$default" > "$override"
grep -q '^sha1.second_preimage_resistance = 2030-02-01 ' "$override" || {
  echo "customize.sh: $default has no sha1.second_preimage_resistance line to extend" >&2
  exit 1
}
