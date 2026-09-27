#!/bin/sh
# Install the extra apt repositories that `scripts/apt-sources.py write` put
# next to this script, then `apt-get update`, and fail unless every one of
# them was fetched and its signature verified.
#
# Run as root in a fresh Debian container (build-deb does; so can a
# repository's install test):   sh <dir>/install.sh
#
# Needs only apt: the keys were fetched when the directory was written.
set -eu
here=$(cd "$(dirname "$0")" && pwd)

if [ ! -s "$here/checks" ]; then
  echo "apt-sources: no extra apt repositories for this suite"
  exit 0
fi
if [ "$(id -u)" != 0 ]; then
  echo "apt-sources: error: run this as root" >&2
  exit 1
fi

# apt fetches https:// itself, but needs the CA certificates to trust it.
if grep -q '^deb [^ ]* https://' "$here"/sources.list.d/*.list &&
   [ ! -e /etc/ssl/certs/ca-certificates.crt ]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates
fi

install -d -m 0755 /etc/apt/keyrings /etc/apt/sources.list.d
install -m 0644 "$here"/keyrings/*.gpg /etc/apt/keyrings/
install -m 0644 "$here"/sources.list.d/*.list /etc/apt/sources.list.d/
cat "$here"/sources.list.d/*.list

log=$(mktemp)
status=0
apt-get update > "$log" 2>&1 || status=$?
cat "$log"

# apt-get update exits 0 when a source can't be reached ("Some index files
# failed to download. They have been ignored"), so exit status alone isn't
# enough: each source must also be in use, which it is only once its Release
# was fetched and verified and its index downloaded.
policy=$(apt-cache policy)
failed=0
while IFS=' ' read -r name source; do
  [ -n "$name" ] || continue
  if ! printf '%s\n' "$policy" | grep -qF " $source "; then
    echo "::error::apt-sources: the $name repository ($source) was not fetched and verified: its key, its Release file or its address is wrong" >&2
    grep -F "${source%% *}" "$log" | grep -E '^(W|E|Err):' >&2 || true
    failed=1
  fi
done < "$here/checks"
rm -f "$log"
if [ "$status" != 0 ]; then
  echo "::error::apt-sources: apt-get update failed (exit $status)" >&2
  failed=1
fi
exit "$failed"
