#!/bin/bash
# Run inside a distribution container. Prove this release's apt can install
# from the repository the action published, through each published keyring,
# and that the check would have caught the original bug.
#
# Usage: tests/apt-client-check.sh <apt-root> <suite> <keyring-stem>
set -u

root=$1
suite=$2
stem=$3

# apt uses Sequoia's sqv when installed and falls back to gpgv otherwise. The
# two disagree about mis-typed keyrings, so which one ran is the whole story.
if command -v sqv > /dev/null; then verifier=sqv; else verifier=gpgv; fi
echo "apt $(dpkg-query -W -f='${Version}' apt), signature verifier: $verifier"

# Only the repository under test: none of the image's own sources.
list=/etc/apt/selftest.list
parts=$(mktemp -d)
apt_opts=(-o "Dir::Etc::SourceList=$list" -o "Dir::Etc::SourceParts=$parts")
failures=0

# attempt <label> <published file> <installed keyring path> <ok|fail>
attempt() {
  local label=$1 src=$2 dest=$3 expect=$4 log got
  rm -rf /etc/apt/keyrings /var/lib/apt/lists/*
  install -d -m 0755 /etc/apt/keyrings
  install -m 0644 "$src" "$dest"
  echo "deb [signed-by=$dest] file:$root/$suite/ ./" > "$list"
  log=$(mktemp)
  # A mis-typed keyring is only a warning on the keyring itself; what fails is
  # the signature, so check both the exit status and the text.
  if apt-get "${apt_opts[@]}" update > "$log" 2>&1 \
     && ! grep -Eq 'unsupported filetype|NO_PUBKEY|not signed|GPG error' "$log" \
     && apt-get "${apt_opts[@]}" install -y --no-install-recommends \
          apt-repo-selftest >> "$log" 2>&1 \
     && dpkg -s apt-repo-selftest > /dev/null 2>&1; then
    got=ok
  else
    got=fail
  fi
  dpkg --purge apt-repo-selftest > /dev/null 2>&1 || true
  if [ "$got" = "$expect" ]; then
    echo "PASS  $label: $got, as expected"
  else
    echo "FAIL  $label: $got, expected $expect"
    sed 's/^/      /' "$log"
    failures=$((failures + 1))
  fi
  rm -f "$log"
}

attempt "binary $stem.gpg installed as .gpg" \
  "$root/$stem.gpg" "/etc/apt/keyrings/$stem.gpg" ok
attempt "armoured $stem.asc installed as .asc" \
  "$root/$stem.asc" "/etc/apt/keyrings/$stem.asc" ok

# Negative control: before the fix the action served armoured bytes under the
# .gpg name. gpgv cannot read that and sqv can. If this does not fail under
# gpgv, the check cannot see the bug and the passes above prove nothing.
if [ "$verifier" = gpgv ]; then want=fail; else want=ok; fi
attempt "armoured bytes installed as .gpg (the original bug)" \
  "$root/$stem.asc" "/etc/apt/keyrings/$stem.gpg" "$want"

[ "$failures" -eq 0 ]
