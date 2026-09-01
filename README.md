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

Most callers want the **reusable workflow** — it owns the `pages: write` /
`id-token: write` permissions and the `github-pages` environment, which a
composite action cannot declare on a caller's behalf.

## Usage

```yaml
name: Debian packages
on:
  push:
    branches: [main]

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
    needs: build
    uses: mithro/apt-repo-action/.github/workflows/publish-apt.yml@main
    with:
      suites: "bookworm trixie sid"
      architectures: "amd64 arm64 armhf riscv64"
      keyring-name: my-project.gpg
    secrets:
      gpg-private-key: ${{ secrets.APT_GPG_PRIVATE_KEY }}
```

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

Each repository has **its own** signing key, published in its own repository
root as `<keyring-name>` and referenced by consumers with `signed-by=`. The
private half is the `APT_GPG_PRIVATE_KEY` repository secret.

`action.yml` **refuses to publish an unsigned repository**. An unsigned repo can
only be consumed with `[trusted=yes]`, which is not an acceptable default.

## Limitations

- `build-deb` always pulls `debian:<suite>`; Ubuntu suites need an image-name
  input. Not yet implemented.
- Suites are indexed as `Suite: stable` regardless of the codename, matching the
  existing repositories.
