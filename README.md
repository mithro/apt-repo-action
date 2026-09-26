# apt-repo-action

Shared GitHub Actions for publishing Debian packages from a source repository as
a **signed, multi-distribution APT repository on GitHub Pages**.

One flat repository per suite (`<pages-url>/<suite>/`), so the same package
version can ship for bookworm, trixie and sid without filenames colliding.

Every repository published with it looks the same: key `<repo>.gpg`, keyring
`/etc/apt/keyrings/<repo>.gpg`, `Origin: <repo>`, one generated index page.
**[docs/conventions.md](docs/conventions.md)** is the convention for the published
repository, and **[docs/packaging.md](docs/packaging.md)** the convention for
building the packages: branches, workflow names, suites, architectures and
versions. This README covers using the workflow.

## What is here

| path | kind | what it does |
|---|---|---|
| `action.yml` | composite | Index and sign a tree of per-suite `.deb` directories |
| `.github/workflows/publish-apt.yml` | reusable workflow | Collect build artifacts → index+sign → deploy to Pages |
| `build-deb/action.yml` | composite | `dpkg-buildpackage` in `debian:<suite>` for one architecture |
| `scripts/make-index.py` | script | Generate the repository landing page |
| `scripts/check-keyrings.py` | script | Fail the publish if a keyring's format contradicts its extension |
| `scripts/carry-over.py` | script | Keep serving a previous layout, frozen, while clients move (`legacy-paths`) |
| `scripts/keep-history.py` | script | Keep earlier package versions from the live site, up to `size-limit-mb` |
| `tests/` + `.github/workflows/selftest.yml` | self-test | Publish with this checkout, then install from it on bookworm, trixie, jammy and noble |

Most callers want the **reusable workflow** — it owns the `pages: write` /
`id-token: write` permissions and the `github-pages` environment, which a
composite action cannot declare on a caller's behalf.

## Usage

The `deb.yml` every repository uses, in outline; [docs/packaging.md](docs/packaging.md)
fixes each name and value.

```yaml
name: Debian packages
on:
  push:
    branches: [main]      # the default branch: `packaging` for a fork
  pull_request:           # preview packages, never published
  workflow_dispatch:

# Least privilege by default. Only the publish job raises this, and only for
# itself -- see "Permissions" below.
permissions:
  contents: read

concurrency:
  group: deb-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  build-deb:
    name: build-deb (${{ matrix.suite }} ${{ matrix.arch }})
    strategy:
      fail-fast: false
      matrix:
        suite: [trixie, forky, sid]
        arch: [amd64, i386, arm64, armhf, riscv64]
    runs-on: ubuntu-latest
    steps:
      - name: Check out
        uses: actions/checkout@v4
        with:
          fetch-depth: 0        # the version needs history + tags
      - name: Build
        uses: mithro/apt-repo-action/build-deb@main
        with:
          suite: ${{ matrix.suite }}
          arch: ${{ matrix.arch }}
      - name: Upload
        uses: actions/upload-artifact@v4
        with:
          name: debs-${{ matrix.suite }}-${{ matrix.arch }}
          path: built-debs/*.deb
          retention-days: 14

  publish-apt:
    # Never publish from a pull request: that would overwrite the live
    # repository with packages built from unreviewed code.
    if: github.event_name != 'pull_request' && github.ref_name == github.event.repository.default_branch
    needs: build-deb
    # Scoped to this job alone, NOT to the workflow.
    permissions:
      contents: read
      pages: write
      id-token: write
    uses: mithro/apt-repo-action/.github/workflows/publish-apt.yml@main
    with:
      suites: "trixie forky sid"
      architectures: "amd64 i386 arm64 armhf riscv64"
      description: "What these packages are, in one line"
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

The artifact name **must** be `debs-<suite>` or start `debs-<suite>-`, normally `debs-<suite>-<arch>`;
further suffixes are allowed (`debs-bookworm-armhf-openocd-stable`). The publish
workflow regroups each artifact under the longest suite in `suites` that its
name starts with, so a suite may contain a dash (`debs-raspbian-trixie-armhf`).

The index page is generated for every repository. To say something about the
packages, put an HTML fragment in `packaging/apt-intro.html`.

## Suites, architectures and versions

All three are fixed by [docs/packaging.md](docs/packaging.md):

- **Suites**: Debian stable, testing and unstable (trixie, forky, sid) plus the
  Raspbian releases of the same codenames. bookworm only for the reasons
  listed there.
- **Architectures**: amd64, i386, arm64, armhf and riscv64, or `all` alone.
  A smaller set only for hardware-specific packages.
- **Versions**: from `git describe` counts, never dates. Every push to the
  default branch is a new, higher version, with a `~deb<R>` suffix per suite
  and `~pr<P>` for pull request previews.

Earlier versions stay published, and installable as `<package>=<version>`,
while the site fits in `size-limit-mb` (default 900 MB of GitHub Pages' 1 GB):
see "One layout" in [docs/conventions.md](docs/conventions.md). A repository
with large packages keeps fewer; `size-limit-mb: 0` keeps none.

## Does a shared *build* action make sense?

Partly, and `build-deb/` is the part that does. Across the repositories using
this pattern the build step falls into three families:

1. **`dpkg-buildpackage` from a `debian/` directory** — the majority. The only
   things that vary are the suite, the architecture and the extra build
   dependencies. This is worth sharing, and is what `build-deb/` implements.
2. **Go projects packaged with `nfpm`** — cross-compilation and an `nfpm.yaml`
   replace the whole Debian toolchain. Almost nothing is shared with (1) beyond
   the artifact naming, so these keep their own build job.
3. **`Architecture: all`** (shell, pure Python) — one build per suite. These
   are family (1) with `arch: all`: `build-deb` builds them once per suite, on
   the runner's own architecture.

So: share the publish half everywhere, and the build half for every
`dpkg-buildpackage` repository. Family (2) uploads `debs-<suite>-<arch>`
artifacts itself and calls the publish workflow unchanged.

`build-deb` stamps the version with the shared
[`scripts/deb-version.py`](scripts/deb-version.py) (Set B, with the `~deb<R>`
and `~pr<P>` suffixes of [docs/packaging.md](docs/packaging.md#versions)). A
repository that still carries its own `packaging/deb-version.py` keeps using
it, with a warning, until it is migrated: see
[Moving a repository to the shared build](docs/packaging.md#moving-a-repository-to-the-shared-build).

## Signing

Each repository has **its own** signing key, referenced by consumers with
`signed-by=`. The private half is the `APT_GPG_PRIVATE_KEY` repository secret.

`action.yml` **refuses to publish an unsigned repository**. An unsigned repo can
only be consumed with `[trusted=yes]`, which is not an acceptable default.

The key is published as `<repo>.gpg` (binary) and `<repo>.asc` (armoured). See
[docs/signing.md](docs/signing.md) for why both, and for the self-test. The
`keyring-name` and `origin` inputs are deprecated: a value other than the
repository name still works, with a warning, until its caller is migrated.

## Limitations

- `build-deb` always pulls `debian:<suite>`; Ubuntu suites need an image-name
  input. Not yet implemented.
- Suites are indexed as `Suite: stable` regardless of the codename, matching the
  existing repositories.
