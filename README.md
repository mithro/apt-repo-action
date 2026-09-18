# apt-repo-action

Shared GitHub Actions for publishing Debian packages from a source repository as
a **signed, multi-distribution APT repository on GitHub Pages**.

One flat repository per suite (`<pages-url>/<suite>/`), so the same package
version can ship for bookworm, trixie and sid without filenames colliding.

## What is here

| path | kind | what it does |
|---|---|---|
| `action.yml` | composite | Index and sign a tree of per-suite `.deb` directories |
| `.github/workflows/publish-apt.yml` | reusable workflow | Collect build artifacts → index+sign → deploy to Pages |
| `build-deb/action.yml` | composite | `dpkg-buildpackage` in `debian:<suite>` for one architecture |
| `scripts/make-index.py` | script | Generate the repository landing page |
| `scripts/check-keyrings.py` | script | Fail the publish if a keyring's format contradicts its extension |
| `tests/` + `.github/workflows/selftest.yml` | self-test | Publish with this checkout, then install from it on bookworm, trixie, jammy and noble |

Most callers want the **reusable workflow** — it owns the `pages: write` /
`id-token: write` permissions and the `github-pages` environment, which a
composite action cannot declare on a caller's behalf.

## Usage

```yaml
name: Debian packages
on:
  push:
    branches: [main]
  pull_request:

# Least privilege by default. Only the publish job raises this, and only for
# itself -- see "Permissions" below.
permissions:
  contents: read

jobs:
  build:
    strategy:
      fail-fast: false
      matrix:
        suite: [bookworm, trixie, sid]
        arch: [amd64, arm64, armhf, riscv64]
        exclude:
          # riscv64 is only an official Debian architecture from trixie onwards.
          - suite: bookworm
            arch: riscv64
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # deb-version.py needs history + tags
      - uses: mithro/apt-repo-action/build-deb@main
        with:
          suite: ${{ matrix.suite }}
          arch: ${{ matrix.arch }}
          build-deps: libfoo-dev
      - uses: actions/upload-artifact@v4
        with:
          name: debs-${{ matrix.suite }}-${{ matrix.arch }}
          path: built-debs/*.deb

  publish:
    # Never publish from a pull request: that would overwrite the live
    # repository with packages built from unreviewed code.
    if: github.event_name != 'pull_request'
    needs: build
    # Scoped to this job alone, NOT to the workflow.
    permissions:
      contents: read
      pages: write
      id-token: write
    uses: mithro/apt-repo-action/.github/workflows/publish-apt.yml@main
    with:
      suites: "bookworm trixie sid"
      architectures: "amd64 arm64 armhf riscv64"
      keyring-name: my-project.gpg
    secrets:
      gpg-private-key: ${{ secrets.APT_GPG_PRIVATE_KEY }}
```

## Permissions

Put the Pages permissions on the **publish job**, not at workflow level.

A job that calls a reusable workflow carries its own `permissions` block, and
that becomes the maximum available to the called workflow. Granting
`pages: write` and `id-token: write` at workflow level instead hands those
scopes to every job — including build jobs that run Docker, clone third-party
sources, and on pull requests compile unreviewed code. None of those should
hold a token that can write to Pages.

The failure mode if you grant too little is loud rather than subtle: the run
ends in `startup_failure` before any job begins, because the called workflow
cannot escalate beyond its caller.

The artifact name **must** be `debs-<suite>-<arch>`; the publish workflow splits
on that to regroup artifacts per suite. Suite and architecture names contain no
dashes, so the split is unambiguous.

## Which suites to build

- **trixie + sid** — everything.
- **+ bookworm** — anything consumed by fpgas.online, whose Pi NFS root is still
  Raspberry Pi OS bookworm.
- **Ubuntu suites** (noble, jammy) are supported by the same machinery: pass them
  in `suites` and build them in the matrix. `debian:<suite>` becomes
  `ubuntu:<suite>`, which `build-deb` does not yet do — see *Limitations*.

## Which architectures

At least `amd64`, `arm64`, `armhf` (older Raspberry Pi) and `riscv64` — except
for packages specific to the Raspberry Pi, which only need `arm64` and `armhf`.

`riscv64` has no bookworm archive; `build-deb` fails that combination with an
explicit message rather than a confusing apt error, and the example matrix
excludes it.

## Versioning

Rolling releases: every push to `main` publishes. Versions come from
`git describe`, via `packaging/deb-version.py` in the calling repository —
`X.Y` at tag `vX.Y`, `X.Y.postN` N commits later. That increments on every
commit, so each push is a new upgradeable version with no manual bump.

`build-deb` calls it with `--write-changelog` so `dpkg-buildpackage` picks the
version up from `debian/changelog`.

## Does a shared *build* action make sense?

Partly, and `build-deb/` is the part that does. Across the repositories using
this pattern the build step falls into three families:

1. **`dpkg-buildpackage` from a `debian/` directory** — the majority. The only
   things that vary are the suite, the architecture and the extra build
   dependencies. This is worth sharing, and is what `build-deb/` implements.
2. **Go projects packaged with `nfpm`** — cross-compilation and an `nfpm.yaml`
   replace the whole Debian toolchain. Almost nothing is shared with (1) beyond
   the artifact naming, so these keep their own build job.
3. **Pure-Python, `Architecture: all`** — one build, no matrix at all. Sharing a
   matrix-shaped action here would add ceremony rather than remove it.

So: share the publish half everywhere, share the build half only for family (1).
Families (2) and (3) upload `debs-<suite>-<arch>` artifacts themselves and call
the publish workflow unchanged.

## Signing

Each repository has **its own** signing key, referenced by consumers with
`signed-by=`. The private half is the `APT_GPG_PRIVATE_KEY` repository secret.

`action.yml` **refuses to publish an unsigned repository**. An unsigned repo can
only be consumed with `[trusted=yes]`, which is not an acceptable default.

### The public key is published twice, and the extension matters

apt decides how to parse a keyring from its **file extension**, not from its
contents:

| file | format apt expects |
|---|---|
| `<stem>.gpg` | binary OpenPGP keyring |
| `<stem>.asc` | ASCII-armoured public key block |

Both are published at the repository root, derived from the stem of
`keyring-name`, so `signed-by=` can point at either as long as the path you
install the key to keeps the extension that matches what you downloaded. Pass
`keyring-name: my-project.gpg` and you get `my-project.gpg` (binary) and
`my-project.asc` (armoured).

Getting this wrong is not a warning, it is a broken repository. gpgv reads the
armour header as an OpenPGP packet, fails, and apt gives up:

```
W: The key(s) in the keyring /etc/apt/keyrings/my-project.gpg are ignored as
   the file has an unsupported filetype.
E: The repository 'https://... trixie ./ InRelease' is not signed.
```

It hides easily: apt prefers Sequoia's `sqv` when that is installed (Debian 13),
and `sqv` accepts either encoding. Everywhere `sqv` is absent — bookworm, Ubuntu
jammy/noble, any apt 2.x — apt falls back to `gpgv` and the repository cannot be
used at all. So the publish runs `scripts/check-keyrings.py`, which fails the
build if either file's contents contradict its extension.

The generated landing page leads with the binary `.gpg` keyring and offers the
`.asc` alongside it.

### Hosts that already installed the key

Until this was fixed, `<stem>.gpg` held armoured bytes. apt never re-downloads
a keyring, so a host that saved that file straight to a `.gpg` path keeps the
unreadable copy after the publisher is fixed. Hosts using `sqv` do not notice;
elsewhere, fetch it again:

```sh
curl -fsSL https://<owner>.github.io/<repo>/<stem>.gpg \
  | sudo tee /etc/apt/keyrings/<stem>.gpg > /dev/null
```

Hosts that piped the key through `gpg --dearmor` are fine either way:
`--dearmor` of binary input is a byte-for-byte no-op (checked on GnuPG 2.2.40
and 2.4.7).

## Testing

`.github/workflows/selftest.yml` runs this checkout of the action against a
throwaway package and key, then `tests/apt-client-check.sh` installs from the
result in `debian:bookworm`, `debian:trixie`, `ubuntu:jammy` and
`ubuntu:noble`. That covers both verifiers apt uses: `gpgv` on the first three
and `sqv` on trixie. Each run also installs the armoured bytes under the `.gpg`
name and expects that to fail under `gpgv`, so a green run shows the check can
still catch the original bug.

Run one client locally, after building a repository into `apt-repo/`:

```sh
docker run --rm -v "$PWD/apt-repo:/repo:ro" -v "$PWD/tests:/tests:ro" \
  debian:bookworm bash /tests/apt-client-check.sh /repo bookworm <stem>
```

## Limitations

- `build-deb` always pulls `debian:<suite>`; Ubuntu suites need an image-name
  input. Not yet implemented.
- Suites are indexed as `Suite: stable` regardless of the codename, matching the
  existing repositories.
