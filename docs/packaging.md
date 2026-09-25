# Packaging conventions

How every Debian package published from `github.com/mithro/*` and
`github.com/fpgas-online/*` is built. [conventions.md](conventions.md) covers
what the published apt repository looks like (key names, layout, setup
lines); this document covers everything before that: where the packaging
lives, how builds are triggered and named, which suites and architectures
are built, and how versions are made.

**MUST** and **SHOULD** are used as in RFC 2119. Anything a repository does
differently from a MUST, or from a default, is an *exception*. Exceptions
are listed, with the reason, under [Recorded exceptions](#recorded-exceptions).
An exception that isn't listed there is a bug.

In what follows, `<repo>` is the GitHub repository name, and `<owner-tag>` is
`welland` for `mithro/*` and `fpgasonline` for `fpgas-online/*` (see
[Versions](#versions)).

## Two kinds of repository

Every packaging repository is one of two kinds. Look at whose code it is:

| | Set A: someone else's code | Set B: our code |
|---|---|---|
| what it is | An upstream project we package, usually with our own patches | A project we wrote |
| examples | dnsmasq, netplan, tmux, usdr-lib | rpi-hwid, sensors2mqtt, nfsroot-watchdog |
| default branch | **`packaging`** | **`main`** |
| upstream history | kept, on the `upstream` branch | n/a |
| `debian/` | at the root of `packaging` | at the root of `main` |
| version | upstream's version + `+<owner-tag><M>` | from our own `git describe` |

Everything after [Set B](#set-b-our-code) applies to both.

### Set A: someone else's code

- **The repository carries upstream's history.** When upstream is on GitHub,
  it MUST be a GitHub fork of upstream. When upstream isn't on GitHub, it
  MUST be imported with its full history (`git clone` + `git push`, or
  `git svn`/`git cvsimport` for a non-git upstream). It must never be a flat
  snapshot of upstream's files: without the history there is no way to see
  what we changed, merge a new upstream release, or send a patch back.
- **`upstream`** is an unmodified mirror of the upstream branch we build from
  (usually upstream's default branch). Only fast-forwards; nothing of ours is
  ever committed to it.
- **`packaging`** is the default branch. It is `upstream` plus our work:
  - our patches, as ordinary commits, one logical change each;
  - `debian/`, at the root;
  - `packaging/` (helper scripts, `README.md`, `apt-intro.html`);
  - `.github/workflows/`.
- **New upstream versions are merged, never rebased.** Rebasing the default
  branch needs a force push and rewrites what was published. The
  `sync-upstream.yml` workflow (see [Workflows](#workflows)) fast-forwards
  `upstream` and opens a pull request merging it into `packaging`.
- **Start `debian/` from Debian's** when Debian packages the software:
  - take it from Debian's packaging repository (salsa.debian.org), keep
    Debian's `debian/changelog` history, and change as little as possible;
  - record the salsa commit it came from in `packaging/README.md`;
  - the package names stay Debian's, so our build replaces Debian's on
    upgrade.
- **Every commit on `packaging` that changes upstream's files** SHOULD say
  where the change stands upstream, as a trailer. Either
  `Upstream: <URL of the pull request or mailing-list post>` or
  `Upstream: not-for-upstream (<reason>)`.
- **`packaging/README.md`** MUST say:
  - which upstream, branch and tag or commit the build follows;
  - what we change, and why;
  - how to update to a new upstream version.

**Backports** are a Set A variant. They rebuild a Debian source package for
an older suite without changing it (paho-mqtt-bookworm rebuilds trixie's
`python-paho-mqtt` for bookworm). The upstream is Debian's archive, so there
is no `upstream` branch. `packaging/` names the exact source version and its
`.dsc` checksum, and the version follows Debian's backport form (see
[Versions](#versions)).

### Set B: our code

- The default branch is **`main`**, and `debian/` is at its root.
- Releases are annotated tags `vX.Y` or `vX.Y.Z`, made by hand when the
  version *should* change: a new feature set or a compatibility break. CI
  never creates `v*` tags. Every push is already a new version (see
  [Versions](#versions)).
- **Patch series** are a Set B variant: our repository holds patches and
  build scripts for software fetched at build time, at a pinned commit.
  fpgas.online-fpga-tools (openFPGALoader, OpenOCD) and rpi-qemu (QEMU) work
  this way.
  - The pins live in one file in the repository, so a pin bump is a commit
    and so a new version.
  - The `debian/` templates may live under `packaging/debian/<name>/`, since
    there is one per fetched project.

## Workflows

Names are fixed, so every repository reads the same in the Actions tab, in
branch protection rules and in links.

| file | `name:` | when |
|---|---|---|
| `.github/workflows/deb.yml` | `Debian packages` | always |
| `.github/workflows/sync-upstream.yml` | `Sync upstream` | Set A only |

**`deb.yml`**:

```yaml
name: Debian packages

on:
  push:
    branches: [<default branch>]   # main (Set B) or packaging (Set A)
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: deb-${{ github.ref }}
  # A newer push to a pull request cancels the older build. On the default
  # branch a running publish is never cancelled: the newest push waits for it,
  # and replaces any older push still waiting (it contains that one's commits).
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  test:           # optional: the project's own test suite
  build-deb:      # matrix: suite x arch
  publish-apt:    # calls publish-apt.yml
  release:        # optional, see "GitHub Releases"
```

- **Triggers**: exactly `push` to the default branch, `pull_request` and
  `workflow_dispatch`. A `schedule:` is allowed only to rebuild something
  that changes without a commit here: a backport tracking Debian, or a
  snapshot tracking upstream. Nothing else: no `workflow_run`. It always
  runs the default branch's copy of the workflow, so a pull request can't
  change or test its own build. Tests are a job in `deb.yml` that
  `build-deb` needs.
- **Job names** are exactly `test`, `build-deb`, `publish-apt` and
  `release`. The matrix job's display name is
  `build-deb (${{ matrix.suite }} ${{ matrix.arch }})`.
- **Steps** in `build-deb` are named, in order:

  | step | does |
  |---|---|
  | `Check out` | `actions/checkout` with `fetch-depth: 0` (the version needs history and tags) |
  | `Build` | the shared build (see [The shared actions](#the-shared-actions)) |
  | `Install test` | install the built packages into a clean container of the suite and run the smoke test |
  | `Upload` | upload `debs-<suite>-<arch>` (and `dbgsym-<suite>-<arch>`, see [Package contents](#package-contents)) |

- **Artifacts** are named `debs-<suite>-<arch>`, with an optional further
  `-<part>` (`debs-bookworm-armhf-openocd-stable`), and kept for
  `retention-days: 14`.
- **`publish-apt`** runs only on the default branch, never for a pull
  request:

  ```yaml
    publish-apt:
      if: github.event_name != 'pull_request' && github.ref_name == github.event.repository.default_branch
      needs: build-deb
      permissions:
        contents: read
        pages: write
        id-token: write
      uses: mithro/apt-repo-action/.github/workflows/publish-apt.yml@main
      with:
        suites: "<suites>"
        architectures: "<architectures>"
        description: "<one line: what these packages are>"
      secrets:
        gpg-private-key: ${{ secrets.APT_GPG_PRIVATE_KEY }}
  ```

**`sync-upstream.yml`** (Set A) runs weekly and on `workflow_dispatch`:
1. Fast-forward `upstream` from the upstream repository.
2. If `packaging` doesn't already contain it, open (or update) a pull
   request titled `Merge upstream <describe>` from a branch named
   `sync/upstream` into `packaging`.

A backport's sync checks Debian's archive for a newer source version instead.

## Builds

- **Every push to the default branch builds and publishes.** There is no
  separate release step for the apt repository.
- **Every pull request builds preview packages**:
  - the same suites and architectures as the default branch, so a pull
    request that breaks one architecture fails before it merges;
  - uploaded as workflow artifacts only, never signed or published;
  - versioned lower than the default branch's (see [Versions](#versions)).
- **Only binary packages are built and published** (`dpkg-buildpackage -b`).
  No source packages; the git repository is the source.
- **The build runs in the suite's own image**: `debian:<codename>`, or a
  Raspbian root file system for `raspbian-<codename>`. Build dependencies
  come from `debian/control` (`apt-get build-dep ./`), never from a list in
  the workflow.
- **Architectures the runner can't execute build under QEMU user emulation.**
  arm64 and armhf run on `ubuntu-24.04-arm`; amd64 and i386 on
  `ubuntu-24.04`.
  - A package whose tests are too slow or unreliable under emulation MAY
    set `DEB_BUILD_OPTIONS=nocheck` for the emulated architectures only.
  - Native builds always run the tests.
- **Builds are reproducible.** `SOURCE_DATE_EPOCH` is the build commit's
  committer time, and the generated changelog entry uses that time too.
- **The install test** installs the built `.deb`s into a clean container of
  each suite, on at least one native architecture, with dependencies from
  the Debian archive. It then runs `packaging/install-test.sh` if present,
  otherwise `<command> --version` for each package that ships a command.

## Suites

The default is every Debian suite that is supported and moving: stable,
testing and unstable, plus the Raspbian releases of the same codenames. On
2026-09-25 that is:

| suite | what | default |
|---|---|---|
| `trixie` | Debian 13, stable | yes |
| `forky` | Debian 14, testing | yes |
| `sid` | unstable | yes |
| `raspbian-trixie` | Raspbian 13, 32-bit Raspberry Pi OS (ARMv6 armhf) | yes, for architecture-dependent packages |
| `raspbian-forky` | Raspbian 14 | yes, for architecture-dependent packages |
| `bookworm` | Debian 12, oldstable | opt-in only, see below |
| `raspbian-bookworm` | Raspbian 12 | with `bookworm` |

- Suite directories are named by **codename**, never by alias (`stable`), so
  a Debian release doesn't silently change what a directory holds.
- Raspbian has no sid. 64-bit Raspberry Pi OS is Debian arm64 plus
  `archive.raspberrypi.com`, so the Debian suites serve it.
- A repository whose packages are all `Architecture: all` publishes only the
  Debian suites. Raspbian hosts use the Debian suite of the same codename:
  the packages are the same files.
- **When Debian makes a release**, this table changes in a pull request:
  1. The new testing is added.
  2. The new oldstable stops being a default. Repositories opted in to
     `bookworm` keep it until they drop it.

**Also building `bookworm`** (and `raspbian-bookworm`, when the package is
architecture-dependent) is allowed for exactly these reasons. Each
repository that does so names its reason in
[Recorded exceptions](#recorded-exceptions).

1. **fpgas.online uses it**: anything in `fpgas-online/*`, and anything
   fpgas.online installs. Its Pi NFS root is Raspberry Pi OS bookworm.
2. **NeTV2**: anything that targets NeTV2 hardware or its hosts.
3. **Trivial**: building for bookworm costs nothing, such as an
   `Architecture: all` package whose dependencies bookworm already has.

**Only `bookworm`** is for a backport: a package that exists to make
something else run on bookworm (paho-mqtt-bookworm).

## Architectures

The default is every 32- and 64-bit x86, ARM and RISC-V architecture that
Debian has:

| arch | what | notes |
|---|---|---|
| `amd64` | x86, 64-bit | |
| `i386` | x86, 32-bit | still a Debian architecture in trixie, forky and sid |
| `arm64` | ARM, 64-bit | also serves 64-bit Raspberry Pi OS |
| `armhf` | ARM, 32-bit (ARMv7) | in `raspbian-*` suites, the Raspbian ARMv6 build instead |
| `riscv64` | RISC-V, 64-bit | not built for bookworm: it has no official riscv64 |

- There is no 32-bit RISC-V: Debian has no riscv32 port.
- `armel` isn't built: forky dropped it.
- **Or architecture-independent only**: a repository whose packages are all
  `Architecture: all` builds once per suite, on amd64, and passes
  `architectures: all`.
- **A restricted set** is allowed only for hardware-specific packages: code
  that can only run on one piece of hardware. The set is that hardware's
  architectures, and the exception names the hardware:
  - Traverse Ten64 (NXP LS1088A): `arm64`;
  - Raspberry Pi: `arm64`, `armhf`, and the `raspbian-*` suites.
- `architectures:` passed to `publish-apt` lists exactly the architectures
  built, plus `all` when some package is architecture-independent. It never
  lists an architecture that has no packages in a suite.

## Versions

Versions come from git, never from dates, run numbers or hand edits. Every
push to the default branch MUST produce a version greater than everything
already published for that package in that suite. A later build of the same
commit produces the same version.

### Set B

```
<X.Y[.Z]>[.post<N>][~deb<R>][~pr<P>]
```

| part | from |
|---|---|
| `X.Y[.Z]` | the newest `vX.Y[.Z]` tag reachable from the build commit (`git describe --tags --match 'v[0-9]*'`), without the `v`. `0.0` when there is none. |
| `.post<N>` | `N` = commits since that tag (describe's count). Left out when `N` is 0, i.e. the commit is the tag. With no tag, `N` counts every commit. |
| `~deb<R>` | the suite (see below) |
| `~pr<P>` | pull request previews only: `P` is the pull request number |

A **patch series** puts the fetched project's version first, so upgrading
upstream is visible in the version:
`<upstream version>+<owner-tag>.<X.Y.postN>`, for example
`1.1.1.post173+fpgasonline.0.0.post70`. `<upstream version>` comes from
upstream's own `git describe` at the pinned commit.

### Set A

```
<base>+<owner-tag><M>[~deb<R>][~pr<P>]
```

| part | from |
|---|---|
| `<base>` | 1. Debian's version, when `debian/` came from Debian: `1.1.2-7`<br>2. otherwise the upstream release tag, when `upstream` is exactly at one: `2.93-0`<br>3. otherwise `<tag>+git<N>.g<sha7>-0`, with `N` = upstream commits since that tag and `sha7` the upstream commit<br>4. upstream has no tags at all: `0.0+git<N>.g<sha7>-0`, with `N` = all upstream commits |
| `<owner-tag>` | `welland` for `mithro/*`, `fpgasonline` for `fpgas-online/*` |
| `<M>` | commits on `packaging` that aren't on `upstream`: `git rev-list --count upstream..packaging` |

Upstream tags are normalised to a Debian upstream version: drop a leading
`v` or project-name prefix, and turn `-` into `.` (`v2.93` → `2.93`,
`netplan-1.1.2` → `1.1.2`).

The `+<owner-tag>` sorts after Debian's own suffixes (`+deb13u1`, `+b1`),
because `w` and `f` both come after `d` and `b`. So our build stays newer
than a Debian stable update of the same upstream version. Nothing else keeps
it that way: when Debian's version moves past ours, ours stops being
installed. That is the signal to merge the new upstream.

A **backport** keeps Debian's version and adds Debian's backport suffix:
`<Debian version>~bpo<R>+<M>`, for example `2.1.0-1~bpo12+1`.

### The suite and preview suffixes

- **`~deb<R>`**: `R` is the suite's Debian release number: `12` for
  bookworm, `13` trixie, `14` forky, and the same for `raspbian-<codename>`.
  sid builds carry no suffix. Two suites can build the same commit
  differently (each against its own libraries), so the older suite's build
  must sort lower. Then `apt full-upgrade` across a release (trixie to
  forky) replaces it.
- **`~pr<P>`** always goes last. A `~` sorts before everything, even the end
  of the string, so a preview is always older than the default-branch build
  of the same commit and never upgrades over it.

For example, from lowest to highest (checked with `dpkg --compare-versions`):

```
0.3.post134~deb12
0.3.post134~deb13~pr41      preview of the same commit
0.3.post134~deb13
0.3.post134~deb14
0.3.post134                 sid
0.3.post135~deb12           the next push
```

### The changelog

`debian/changelog` in git is not where versions are kept. The build
prepends an entry:
- `<source> (<version>) <suite>; urgency=medium`;
- one line, `Built from <owner>/<repo>@<sha>`;
- the maintainer from [Package contents](#package-contents);
- the build commit's committer time as its date.

Set A repositories whose `debian/` came from Debian keep Debian's entries
underneath.

**Epochs** (`2:`) are never introduced, except to recover from a version
that sorts wrongly. Each one is a recorded exception.

## The shared actions

- Publishing is **only** through
  `mithro/apt-repo-action/.github/workflows/publish-apt.yml@main`:
  - at `@main`, not a commit or tag, so every repository runs the same
    publisher;
  - no repository indexes, signs or deploys an apt repository itself.
- The build and the version are the shared `mithro/apt-repo-action`
  pieces: `build-deb/` today, the reusable `build-deb.yml` workflow once it
  exists (see [compliance-plan.md](compliance-plan.md)), and the shared
  version script. A repository doesn't carry its own copy of
  `deb-version.py`.
- A repository that builds with `nfpm` instead of `dpkg-buildpackage` (Go
  static binaries) still follows every naming, version, suite and
  architecture rule here. Only the `Build` step differs.

## Package contents

- `Maintainer: Tim 'mithro' Ansell <me@mith.ro>`. In Set A packages whose
  `debian/` came from Debian, Debian's maintainer moves to
  `XSBC-Original-Maintainer:`.
- `Homepage:` is the project's home: upstream's for Set A, the GitHub
  repository for Set B.
- `Vcs-Git:` and `Vcs-Browser:` point at our GitHub repository, and for
  Set A at the `packaging` branch (`-b packaging`).
- **Package names**:
  - Set A keeps upstream's and Debian's names, so our build replaces the
    distribution's.
  - Set B names MUST NOT clash with a package in Debian.
  - A variant of someone else's software that should install *alongside*
    theirs takes a suffix (`openocd-fpgasonline`, `python3-paramiko-insecure`).
- **Debug symbols**: `-dbgsym` packages go into the apt repository only up
  to 10 MB each. Larger ones are uploaded as `dbgsym-<suite>-<arch>`
  artifacts and published only on the GitHub Release.
- `lintian` runs on every build. Errors SHOULD be fixed; they don't fail the
  build yet.

## GitHub Releases

Optional. A repository that also publishes its builds as GitHub Releases:
- tags each one `build-<version>`, never `v*` (which is for
  [Set B releases](#set-b-our-code));
- names each `.deb` asset `<suite>_<file>.deb`, since every suite's build
  has the same filename;
- includes everything built, debug symbols too. The apt repository is the
  convenient way to install; the release is the complete record.

## Documentation

- **README.md** (Set B) or **packaging/README.md** (Set A) has an
  `## Install` section with the setup block from
  [conventions.md](conventions.md#one-setup), for each suite. It shows the
  real site URL and key fingerprint.
- `packaging/apt-intro.html` is optional: prose for the index page.
- The GitHub repository description says what the packages are. For Set A
  it names the upstream: "tmux, with …, packaged for Debian".

## Repository settings

- The default branch is `main` (Set B) or `packaging` (Set A).
- Pages is built from GitHub Actions, with HTTPS enforced.
- The signing key is the repository secret `APT_GPG_PRIVATE_KEY`, the
  repository's own key ([conventions.md](conventions.md#one-signing-setup)).
- The `github-pages` environment allows deployments from the default branch
  only.

## Recorded exceptions

Every difference from a default or a MUST above, with its reason. A
repository not listed follows every default.

| repository | exception | reason |
|---|---|---|
| fpgas-online/apt | collects packages built elsewhere: no `debian/`, no build matrix, versions come from each package's own repository | it is fpgas.online's apt repository for small packages that don't warrant their own; each is built and versioned in its own repository |
| fpgas-online/apt | `bookworm` | fpgas.online uses it |
| fpgas-online/fpgas.online-fpga-tools | `bookworm` | fpgas.online uses it; NeTV2 |
| fpgas-online/fpgas.online-fpga-tools | architectures `arm64 armhf` (+ `raspbian-*`) | hardware-specific: Raspberry Pi 5 (RP1 PIO JTAG) |
| fpgas-online/fpgas.online-fpga-tools | `libpio0` / `libpio-dev` versioned by upstream commit date | Raspberry Pi's piolib has no tags or versions. Moving to `0.0+git<N>` needs an epoch, since it sorts below the published `20260914+…`. |
| fpgas-online/nfsroot-watchdog | `bookworm` | fpgas.online uses it |
| fpgas-online/rpi-qemu | epoch `2:` | recovery from an earlier version scheme |
| mithro/paho-mqtt-bookworm | only `bookworm` | backport: paho-mqtt 2.x for bookworm, needed by sensors2mqtt there |
| mithro/python-netgear-switch-library | `bookworm` | fpgas.online uses it |
| mithro/rp1-jtag | not published | retired: its packages moved to fpgas.online-fpga-tools. Its Pages site is replaced by hand (`pages.yml`). |
| mithro/rpi-hwid | `bookworm` | NeTV2 (the `rpi5-netv2` host) |
| mithro/sensors2mqtt | `bookworm` | fpgas.online uses it |
| mithro/ten64-microcontroller-utility | architecture `arm64` | hardware-specific: Traverse Ten64 |
