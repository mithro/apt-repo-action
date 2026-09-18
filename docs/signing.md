# Signing and keyrings

Details of how `action.yml` signs a repository and publishes its public key.
The README covers only what a caller needs to set up.

## The public key is published twice, and the extension matters

apt decides how to parse a keyring from its **file extension**, not from its
contents:

| file | format apt expects |
|---|---|
| `<stem>.gpg` | binary OpenPGP keyring |
| `<stem>.asc` | ASCII-armoured public key block |

Both are published at the repository root, derived from the stem of
`keyring-name`, so `signed-by=` can point at either, as long as the installed
path keeps the extension of the file downloaded. Pass
`keyring-name: my-project.gpg` and you get `my-project.gpg` (binary) and
`my-project.asc` (armoured). Any other extension is rejected before the
private key is imported.

Getting this wrong is not a warning, it is a broken repository. gpgv reads the
armour header as an OpenPGP packet, fails, and apt gives up:

```
W: The key(s) in the keyring /etc/apt/keyrings/my-project.gpg are ignored as
   the file has an unsupported filetype.
E: The repository 'https://... trixie ./ InRelease' is not signed.
```

It hides easily: apt prefers Sequoia's `sqv` when that is installed (Debian 13),
and `sqv` accepts either encoding. Everywhere `sqv` is absent (bookworm, Ubuntu
jammy/noble, any apt 2.x) apt falls back to `gpgv`, and the repository cannot
be used at all. So the publish runs `scripts/check-keyrings.py`, which fails the
build if either file's contents contradict its extension.

The generated landing page leads with the binary `.gpg` keyring and offers the
`.asc` alongside it.

## Hosts that installed the key before the fix

Until mithro/apt-repo-action#2, `<stem>.gpg` held armoured bytes. apt never
re-downloads a keyring, so a host that saved that file straight to a `.gpg`
path keeps the unreadable copy after the publisher is fixed. Hosts using `sqv`
do not notice; elsewhere, fetch it again:

```sh
curl -fsSL https://<owner>.github.io/<repo>/<stem>.gpg \
  | sudo tee /etc/apt/keyrings/<stem>.gpg > /dev/null
```

Hosts that piped the key through `gpg --dearmor` are fine either way:
`--dearmor` of binary input is a byte-for-byte no-op (checked on GnuPG 2.2.40
and 2.4.7).

## The private key on the runner

The sign step imports `APT_GPG_PRIVATE_KEY` into a temporary `GNUPGHOME` and
removes it, and stops its gpg-agent, from an `EXIT` trap, so a failed run does
not leave the key behind. The key is still written to that directory
unencrypted while the step runs; issue #3 tracks protecting it at rest.

## Testing

`.github/workflows/selftest.yml` publishes a throwaway repository with this
checkout of the action, then `tests/apt-client-check.sh` installs from it in
`debian:bookworm`, `debian:trixie`, `ubuntu:jammy` and `ubuntu:noble`. That
covers both verifiers: `gpgv` on the first three and `sqv` on trixie. Each run
also installs the armoured bytes under the `.gpg` name and expects that to fail
under `gpgv`, so a green run shows the check can still see the original bug.
The self-test also fails if a published file contains private key material or
if an imported key is left on the runner.

Run one client locally, after building a repository into `apt-repo/`:

```sh
docker run --rm -v "$PWD/apt-repo:/repo:ro" -v "$PWD/tests:/tests:ro" \
  debian:bookworm bash /tests/apt-client-check.sh /repo bookworm <stem>
```
