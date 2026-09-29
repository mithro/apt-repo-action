# Packaging conventions

How every Debian package published from `github.com/mithro/*` and
`github.com/fpgas-online/*` is built. [conventions.md](conventions.md) covers
what the published apt repository looks like (key names, layout, setup
lines); this document covers everything before that: where the packaging
lives, how builds are triggered and named, which suites and architectures
are built, and how versions are made.

**MUST** and **SHOULD** are used as in RFC 2119. Anything a repository does
differently from a MUST, or from a default, is an *exception*. Each
repository declares its kind and its exceptions, with the reason for each,
in its own [`.github/apt-packaging.toml`](#the-declaration). An exception that
isn't declared there is a bug.

In what follows, `<repo>` is the GitHub repository name, and `<owner-tag>` is
`welland` for `mithro/*` and `fpgasonline` for `fpgas-online/*` (see
[Versions](#versions)).

## Three kinds of repository

Every packaging repository is one of three kinds. Look at whose code it is,
and how we follow it:

| | Set A: someone else's code, forked | Mirror: someone else's code, copied exactly | Set B: our code |
|---|---|---|---|
| what it is | An upstream project we fork and package, usually with our own patches | An exact copy of an upstream project, packaged unchanged | A project we wrote |
| examples | netplan, tmux, usdr-lib | migen (git.m-labs.hk) | rpi-hwid, sensors2mqtt, nfsroot-watchdog |
| default branch | **`packaging`** | **`packaging`**, with no history in common with upstream's | **`main`** |
| upstream history | kept, on the `upstream` branch | kept, on branches under upstream's own names | n/a |
| new upstream commits | merged by a reviewed pull request | published the day they land, unreviewed | n/a |
| `debian/` | at the root of `packaging` | at the root of `packaging` | at the root of `main` |
| version | upstream's version + `+<owner-tag><M>` | upstream's `git describe` + `+<owner-tag>.` ours | from our own `git describe` |

Whether someone else's code is Set A or a mirror is chosen per repository,
and is Tim's call. An upstream that isn't on GitHub is normally a mirror; one
on GitHub is normally Set A, but may be a mirror too. A repository we own or
actively develop is never a mirror (a GitHub fork we actively develop is
Set A). Everything after [Set B](#set-b-our-code) applies to all three.

### Set A: someone else's code

Set A is for an upstream we fork, patch, and bring up to date by a reviewed
pull request, usually one on GitHub. An upstream that isn't on GitHub is
normally a [mirror](#mirrors-someone-elses-code-copied-exactly) instead,
including one that carries [our own patches](#our-own-patches-on-a-mirror).

- **The repository carries upstream's history.** When upstream is on GitHub,
  it MUST be a GitHub fork of upstream. When upstream isn't on GitHub (and
  the repository is Set A anyway, see above), it MUST be imported with its
  full history (`git clone` + `git push`, or `git svn`/`git cvsimport` for a
  non-git upstream). It must never be a flat snapshot of upstream's files:
  without the history there is no way to see what we changed, merge a new
  upstream release, or send a patch back.
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

### Mirrors: someone else's code, copied exactly

A mirror repository packages an upstream it copies exactly: usually one that
isn't on GitHub (migen moved to git.m-labs.hk, and its GitHub repository was
archived), and, by choice per repository, some that are. It keeps two things
apart:
1. **the packaging**: one branch with the build, the sync and nothing else;
2. **exact copies of upstream**, synchronised automatically.

When the sync moves the branch the package is built from, the new upstream
commit is built and published straight away, **with no review**. That is
the point of the kind: the published package follows upstream, and nothing
of ours sits between them. A repository we own or actively develop is never
a mirror.

- **`packaging`** is the default branch. It is an orphan branch: it has no
  history in common with any upstream branch (`git merge-base` finds
  nothing). It holds only what the build and the sync need (typically
  `debian/`, `packaging/`, `.github/`, `README.md` and `.gitignore`), never
  upstream's files. Its first commit is tagged
  `v0.0`, so our half of the version counts from there.
- **Upstream's branches and tags** are here under upstream's own names
  (`master`), as exact copies, force-updated by the sync. Nothing of ours is
  ever committed to one. Branches that are ours besides `packaging` (migen
  keeps the archived GitHub repository's branches) are listed in the
  declaration's `[mirror] ours`, and the sync never touches them.
- **`sync-upstream.yml`** (`Sync upstream`) runs daily and on
  `workflow_dispatch`, and is only a call to the shared
  `mithro/apt-repo-action/.github/workflows/sync-mirror.yml@main`
  (`scripts/sync-mirror.py`), so no mirror carries its own sync:

  ```yaml
  name: Sync upstream
  on:
    schedule:
      - cron: "0 6 * * *"   # daily
    workflow_dispatch:
  permissions:
    contents: write         # push the copies
    actions: write          # start deb.yml
    issues: write           # with [[mirror.patches]]: say when one doesn't apply
  concurrency:
    group: sync-upstream
    cancel-in-progress: false
  jobs:
    sync:
      uses: mithro/apt-repo-action/.github/workflows/sync-mirror.yml@main
      # secrets:
      #   token: ${{ secrets.MIRROR_TOKEN }}   # to copy .github/workflows files
  ```

  If the sync pushed the copies but couldn't start `deb.yml`, the next sync
  sees nothing new: run **Debian packages** by hand (the run says so).

  - it copies every branch and tag of the declared `upstream` here under the
    same name, forced, so each is always identical to upstream's;
  - **it never deletes anything.** A branch or tag upstream deletes stays
    here, with a warning: history is never lost, and a package built from
    it stays reproducible;
  - it never touches what is ours: `packaging`, `[mirror] ours`, the
    [patch branches](#our-own-patches-on-a-mirror) (`patches/*`), and our
    tags (`packaging`'s `v0.0`, any tag on `packaging`'s history, `archive/*`),
    whatever upstream has;
  - when `[mirror] build`, the branch the package is built from, moved, it
    starts `deb.yml` on `packaging` (`gh workflow run deb.yml --ref
    packaging`). A push made with the workflow's own token doesn't start
    other workflows by itself, so the sync has to.

  It needs `contents: write` (to push the copies) and `actions: write` (to
  start `deb.yml`), `issues: write` with patch branches (below), and a
  `concurrency` group that doesn't cancel, so two syncs never race.
  **The workflow token can't push `.github/workflows/` files**, so a mirror
  of an upstream that has any (most on GitHub) needs a token that can:
  a GitHub App token or a fine-grained token with Contents and Workflows
  write, passed as the reusable workflow's `token` secret. That is a
  repository secret, set by its owner.
- **The tag ruleset admits upstream's tags.** A mirror copies upstream's
  tags under their own names, so a ruleset that restricts tag names (our
  "only `vX.Y`" one) MUST also admit upstream's: migen's excludes
  `refs/tags/[0-9]*` as well. `PKG-SYNC` checks each upstream tag against the
  active tag rulesets. A tag a ruleset refuses is skipped, with a warning, and
  never holds up the branches.
- **`deb.yml`** runs on a push to `packaging`, when the sync starts it, and
  on pull requests (which never publish). It checks out `packaging`, and the
  build branch into `src/` (with the history, `fetch-depth: 0`, so the
  pinned patch commits are there too), copies `debian/` into `src/`,
  generates `src/debian/patches/` when there are patches, and builds `src/`.
- **Nothing in upstream's files is changed** except by our
  [patch branches](#our-own-patches-on-a-mirror). What the build needs to
  adapt (migen's `debian/pyproject-compat.py`, for bookworm's setuptools)
  lives in `debian/` and is undone after the build.
- **`README.md`**, at the root of `packaging`, says which upstream, which
  branches are copies and which are ours, how the sync and the build run,
  and how the version is made. It is at the root, not in `packaging/` as in
  Set A, because on a mirror's `packaging` the root is ours: upstream's own
  README is on upstream's branches.

#### Our own patches on a mirror

Someone else's code that carries our own patches is a mirror too: dnsmasq,
with our streaming AXFR and `--dump-config` changes. The copies of upstream
stay exact, `packaging` still holds none of upstream's files, and nobody
maintains patch files by hand:

- **Each change is a branch, `patches/<topic>`** (`patches/streaming-axfr`,
  `patches/dump-config`): our commits, one logical change each, on top of a
  commit of the built branch, or on top of an earlier patch branch (a
  stack: `patches/b` built on `patches/a`, listed after it). A patch branch
  can't change a binary file, or create or delete an empty one: a quilt
  patch can't carry either. They are ours: the sync never pushes to or
  deletes a `patches/*` branch, and never copies an upstream branch under
  that name.
- **The declaration pins each one**, in the order they apply:

  ```toml
  [[mirror.patches]]
  branch = "patches/streaming-axfr"
  commit = "4f1c2d9e…"          # the tip that is built
  [[mirror.patches]]
  branch = "patches/dump-config"
  commit = "a07e55b1…"
  ```

  A build uses the pinned commits, never a moving branch, so it is
  reproducible. Changing a patch is a push to its branch plus a pull request
  to `packaging` moving the pin, and that `packaging` commit is what raises
  the version (its `<X.Y.postN>` half).
- **The build generates `debian/patches/`**: the shared
  `scripts/mirror-patches.py`, run in `deb.yml` before `build-deb`, writes
  `git format-patch <base>..<commit>` for each pin (one patch per commit;
  `<base>` is the earlier pin it is built on, or where the branch leaves the
  built branch) into `debian/patches/<topic>/`, and the `series`.
  `packaging`'s `debian/source/format` is `3.0 (quilt)`, and
  `dpkg-buildpackage -b` applies them before building. `packaging` never
  commits `debian/patches/` (`PKG-PATCHES`).
- **A patch that doesn't apply fails the build, never skips it.**
  `dpkg-source --before-build` tries only the first patch and, when that
  doesn't apply, takes the whole series as already applied, exits 0, and
  the package is built without our patches. So:
  - `mirror-patches.py` applies the series, in order, to a checkout of the
    built commit (as the build's is: not `git archive`, which honours
    `export-subst` and `export-ignore`) with dpkg-source's own `patch`
    options before it writes it, and fails on the first that doesn't apply
    (and on a binary change, or an empty file created or deleted);
  - `build-deb`, given generated patches (`debian/patches/.mirror-patches`),
    applies them itself and fails unless `.pc/applied-patches` is the whole
    series.
- **When upstream moves**, the patches are applied to the new tip as they
  are: while they still apply, nothing needs doing. The sync checks that
  (the same generation and `patch` check the build makes, on the new tip)
  before it starts the build. If one doesn't apply, it **doesn't start the build**, so nothing
  broken is published and the last good package stays, and it opens (or
  updates) an issue saying which patch and which upstream commit.
- **Rebasing a patch branch** is then done by a person, reviewed like any
  other change: tag its old tip `archive/patches/<topic>/<YYYY-MM-DD>`
  first (history is never lost, and pinned commits stay fetchable), rebase
  it onto the built branch's tip, push it, and move the pin in a pull
  request to `packaging`.
- **When upstream takes a patch**, its entry is removed from the
  declaration; the branch stays, as history.

#### Several upstreams (follow-up)

The declaration has one `upstream`. dnsmasq follows four (thekelleys'
`dnsmasq.git` and `dnsmasq-debian.git`, salsa, and dgit), and thekelleys'
`dnsmasq.git` publishes only `master` as a branch, keeping its others as
`refs/remotes/*`. So a follow-up adds, alongside `upstream`, a list:

```toml
[[mirror.upstreams]]
name = "thekelleys"
url = "git://thekelleys.org.uk/dnsmasq.git"
refs = ["refs/heads/*", "refs/remotes/origin/*"]  # source patterns, not only heads
prefix = ""                     # mirrored branch names: <prefix><name>
tags = "v[0-9]*"                # the version tags, for this upstream
tag-prefix = ""                 # tags from another source that collide get one
```

with tag collisions between sources reported in the run summary, never
overwritten. Until then, a mirror has one upstream.

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
| `.github/workflows/sync-upstream.yml` | `Sync upstream` | Set A and mirrors |

**`deb.yml`**:

```yaml
name: Debian packages

on:
  push:
    branches: [<default branch>]   # main (Set B) or packaging (Set A, mirrors)
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
  build-deb:      # uses: build-deb.yml, or its own matrix: suite x arch
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
- **A workflow that follows `deb.yml`** through `workflow_run` (a PyPI
  publish, say) is the other way round, and allowed: it runs after the
  build, never instead of it. It:
  - names the workflow `Debian packages` in `workflows:`, so renaming the
    build workflow means changing it too (rpi-hwid's and
    python-netgear-switch-library's followed `CI` until then);
  - acts only on a successful run that isn't a pull request's. `branches:`
    is the branch "the triggering workflow must run on", and a pull
    request from a fork's own `main` has that name too. The workflow it
    starts "is able to access secrets and write tokens, even if the
    previous workflow was not" (GitHub's docs), so without the check it
    would publish the pull request's commit:

    ```yaml
    if: >-
      github.event_name == 'workflow_dispatch' ||
      (github.event.workflow_run.conclusion == 'success' &&
       github.event.workflow_run.event != 'pull_request')
    ```

    `PKG-TRIGGERS` checks every workflow for this.
  - checks out `${{ github.event.workflow_run.head_sha || github.sha }}`,
    the commit the build tested, not whatever the default branch has moved
    on to since.
- **Job names** are exactly `test`, `build-deb`, `publish-apt` and
  `release`. The matrix job's display name is
  `build-deb (${{ matrix.suite }} ${{ matrix.arch }})`.
- **`build-deb` is the reusable
  [`build-deb.yml`](../.github/workflows/build-deb.yml)** unless the
  repository needs a build job of its own (nfpm, a patch series, steps
  around the build):

  ```yaml
    build-deb:
      needs: test      # when there is a test job
      uses: mithro/apt-repo-action/.github/workflows/build-deb.yml@main
  ```

  A patch series has its own job because the workflow runs nothing before
  the build, and a patch series must first fetch the pinned project and
  render its `debian/` into it. The workflow's `version-tree`,
  `version-args` and `lintian` inputs pass straight through to
  `build-deb`, for a repository that builds its own tree but needs them
  (an epoch under a declared exception, say).

  It reads the suites and architectures from
  [the declaration](#the-declaration), so the workflow lists neither, and
  gives `publish-apt` the suites as its output `suites`. (Its
  `architectures` output is for information: `publish-apt` advertises each
  suite's own architectures, read from its packages.)
  Its jobs read `build-deb / build (trixie amd64)` and
  `build-deb / install-test (trixie amd64)`, and it does everything below
  itself: the step names, the artifacts, the install test (on a native
  architecture, once per suite), `-dbgsym` over 10 MB kept out of apt, and
  the `Architecture: all` packages built in exactly one job per suite.
- **Steps** in a repository's own `build-deb` job are named, in order:

  | step | does |
  |---|---|
  | `Check out` | `actions/checkout` with `fetch-depth: 0` (the version needs history and tags) |
  | `Build` | the shared build (see [The shared actions](#the-shared-actions)) |
  | `Install test` | install the built packages into a clean container of the suite and run the smoke test |
  | `Upload` | upload `debs-<suite>-<arch>` (and `dbgsym-<suite>-<arch>`, see [Package contents](#package-contents)) |

- **Anything else the build makes** is a step of its own in the same
  `build-deb` job, between `Build` and `Install test`, writing its `.deb`s
  into the same `built-debs/`. They are then install-tested with the rest
  and uploaded in the one `debs-<suite>-<arch>` artifact. Not a separate
  job: that would need an artifact of its own, and the install test
  wouldn't see both. ntrip-rtcm3-to-rtcm2p3's `Build pyrtcm and pynmeagps`
  step works this way: it builds, from their PyPI sdists, the two Python
  libraries it needs that Debian lacks (pyrtcm in every suite, pynmeagps
  in trixie).

- **An `Architecture: all` repository** has `arch: [all]` in its matrix, so
  its jobs read `build-deb (trixie all)` and its artifacts `debs-trixie-all`.
- **An existing `ci.yml`** (tests in a workflow of their own) becomes the
  `test` job in `deb.yml`, and the file is deleted. Update the branch
  protection's required checks in the same change: they name the old jobs.
  - Several jobs become one `test` job. Its matrix is the old test
    matrix, and a job that needed only one leg becomes steps of one leg:
    python-netgear-switch-library's `docs` job is now the documentation
    build in its Python 3.13 leg (`include: - python-version: "3.13"
    docs: true`, and `if: matrix.docs` on those steps).
  - Checks that were a `workflow_run` chain before (build only after `CI`
    went green) are now `build-deb`'s `needs: test`.
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
        suites: ${{ needs.build-deb.outputs.suites }}       # or "<suites>" with an own build job
        description: "<one line: what these packages are>"
      secrets:
        gpg-private-key: ${{ secrets.APT_GPG_PRIVATE_KEY }}
  ```

  **Only public repositories can publish this way.** publish-apt lists the
  run's artifacts through the API with its token, which has no
  `actions: read`; a public repository's artifacts can be listed without
  it. Adding `actions: read` to the job above changes nothing: a called
  workflow's token "can be only downgraded (not elevated)" (GitHub's docs),
  and publish-apt.yml's own `permissions:` doesn't include it. A private
  repository needs publish-apt.yml to ask for `actions: read`, and then
  every caller must grant it, or its run ends in `startup_failure`.

**`sync-upstream.yml`** (Set A) runs weekly and on `workflow_dispatch`:
1. Fast-forward `upstream` from the upstream repository.
2. If `packaging` doesn't already contain it, open (or update) a pull
   request titled `Merge upstream <describe>` from a branch named
   `sync/upstream` into `packaging`.

A backport's sync checks Debian's archive for a newer source version instead.
A mirror's sync copies upstream exactly and starts the build itself, with no
pull request (see [Mirrors](#mirrors-someone-elses-code-copied-exactly)).

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
- **A build dependency Debian doesn't have for a suite** comes from a
  *dependency repository*: another apt repository, ours or someone else's,
  declared under [`[[depends]]`](#dependency-repositories) with the reason.
  The build adds the ones declared for its suite before `apt-get build-dep`,
  and fails if any of them can't be fetched and verified. sensors2mqtt's
  bookworm build gets `python3-paho-mqtt (>= 2)` from paho-mqtt-bookworm
  this way.
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
  the Debian archive and from the suite's dependency repositories. It then
  runs `packaging/install-test.sh` if present, otherwise
  `<command> --version` for each package that ships a command.
  - The dependency repositories are the ones the build used: `build-deb`'s
    `apt-sources` output is a directory holding them, and its `install.sh`
    adds them to the container. It needs only apt, and does nothing when
    the suite has none:

    ```yaml
          - name: Install test
            env:
              SUITE: ${{ matrix.suite }}
              APT_SOURCES: ${{ steps.build.outputs.apt-sources }}   # the Build step's id: build
            run: |
              docker run --rm -v "$APT_SOURCES:/apt-sources:ro" -v "$PWD/built-debs:/debs:ro" \
                "debian:$SUITE" sh -ec '
                  sh /apt-sources/install.sh
                  apt-get update
                  apt-get install -y /debs/*.deb
                  ...'
    ```

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
[its declaration](#the-declaration).

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
- Each suite's `Release` advertises exactly the architectures that have
  packages in that suite, plus `all` when some package is
  architecture-independent, and never one with no packages: `publish-apt`
  reads them from the suite's own `Packages`. So a `raspbian-*` suite says
  `armhf`, and bookworm never says `riscv64`. Its `architectures:` input is
  deprecated and doesn't change `Release`.

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
upstream's own `git describe --tags` at the pinned commit: `1.1.1` at its tag
`v1.1.1`, `1.1.1.post173` 173 commits later. A `-` in the tag becomes `~`, so
a release candidate (`v11.0.0-rc2`, `11.0.0~rc2`) sorts below its release.
A pin that isn't a tag, or a project without usable tags, records the version
with the pin instead (fpga-tools' `upstreams.toml`, libpio's pin date), and
the build passes that. `~deb<R>` and `~pr<P>` follow as for any Set B
version.

When the fetched project is **a Debian source package** (cryptography-insecure
rebuilds each suite's `python-cryptography`, renamed), `<upstream version>` is
that package's own version, revision included, pinned per suite:
`43.0.0-3+deb13u1+welland.0.0.post6~deb13`. A `3.0 (quilt)` source needs the
revision, and ours extending Debian's keeps a Debian stable update
(`+deb13u2`) or a new Debian version sorting above every build of the old one.
The build passes `--upstream-debian-version 43.0.0-3+deb13u1`, and the
fetched source's `debian/changelog`, Debian's history, stays under the
build's entry.

An **epoch** (`2:`) is only for a repository recovering from an earlier
version scheme, and is declared as a `PKG-VERSION` exception (rpi-qemu).

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

### Mirrors

A mirror uses the [patch series](#set-b) form,
`<upstream version>+<owner-tag>.<X.Y.postN>[~deb<R>][~pr<P>]`, from the
shared script: `scripts/deb-version.py --upstream-dir src --version-tree .
--owner-tag <owner-tag>`, run in `packaging`'s checkout with the build branch
in `src/`.
- `<upstream version>` is upstream's own `git describe --tags --long` on the
  build branch, over the tags matching `[mirror] tags` (default `[0-9]*`,
  migen's bare `0.9.2`; `v[0-9]*` for `v`-prefixed ones), so a packaging or
  experiment tag upstream (`debian/2.90-1`) is never read as a version. The
  tag is normalised: a leading `v` or project-name prefix goes, `-` becomes
  `~` (a release candidate sorts below its release) and `_` becomes `.`.
  migen's `0.9.2` gives `0.9.2.post126` 126 commits later. The script reads
  `[mirror] tags` from the declaration in `--version-tree` itself; a patch
  series passes `--upstream-tag-match` instead, or, without it, any tag
  counts, as before.
- `<X.Y.postN>` is `packaging`'s own `git describe --match 'v[0-9]*'`,
  counted from the `v0.0` tag on its first commit.

migen publishes `0.9.2.post126+fpgasonline.0.0.post7~deb13` this way.
Either a new upstream commit or a new packaging commit raises it.

**When upstream rewrites its history**, the copy follows it, and the upstream
half of the version can go *down* (fewer commits since the tag, or an older
tag): apt then won't upgrade to the new build, and that suite keeps the old
package. The sync warns in its run summary when the built branch's new tip
isn't a descendant of the old one. A new commit on `packaging` doesn't help
(the upstream half is compared first); the way out is an epoch, recorded as
a `PKG-VERSION` exception.

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
  - A pull request builds GitHub's merge of it into the default branch, so
    its count is the count the merge commit will have. The default branch's
    build after the merge then sorts above every preview. A squash or rebase
    merge makes fewer commits, and a preview of a pull request with several
    commits can sort above it. So packaging repositories merge pull requests
    with merge commits.

For example, from lowest to highest (checked with `dpkg --compare-versions`):

```
0.3.post134~deb12
0.3.post134~deb13~pr41      preview of the same commit
0.3.post134~deb13
0.3.post134~deb14
0.3.post134                 sid
0.3.post135~deb12           the next push
```

### Python packages

`~deb<R>` and `~pr<P>` aren't PEP 440, so a Python build can't be given the
Debian version as it is. dh-python gives it anyway: when the build
dependencies include `python3-setuptools-scm`, `python3-flit-scm` or
`python3-hatch-vcs`, pybuild sets `SETUPTOOLS_SCM_PRETEND_VERSION` to the
changelog's version, less revision and epoch, with its first `~` turned
into `-` (`Debian/Debhelper/Buildsystem/pybuild.pm`, dh-python 6.20250414 in
trixie). ntrip-rtcm3-to-rtcm2p3's first shared build failed that way in
every suite:

```
ValueError: Invalid version `0.1.0.post29-deb13~pr5` from source `vcs`
```

pybuild only sets it when it's unset, so `debian/rules` sets it first, to
the version up to the first `-` or `~`: the `X.Y[.postN]` hatch-vcs gets
from git, which the PyPI wheel of the same commit carries. Either form
does it:

```make
# ntrip-rtcm3-to-rtcm2p3, sensors2mqtt
export SETUPTOOLS_SCM_PRETEND_VERSION = $(shell dpkg-parsechangelog -SVersion | sed 's/[-~].*//')
# rpi-hwid, python-netgear-switch-library (native: no revision)
export SETUPTOOLS_SCM_PRETEND_VERSION = $(firstword $(subst ~, ,$(shell dpkg-parsechangelog -SVersion)))
```

### The changelog

`debian/changelog` in git is not where versions are kept. The build writes
an entry:
- `<source> (<version>) <suite>; urgency=medium`;
- one line, `Built from <owner>/<repo>@<sha>`;
- the maintainer from [Package contents](#package-contents);
- the build commit's committer time as its date.

**Set B and mirrors commit no `debian/changelog`**, and list `debian/changelog` in
`.gitignore`, so a local build doesn't dirty the tree (`PKG-CHANGELOG`
checks both). The build's entry is the whole file. Why:
- each package's changelog is one true entry, the build's, not the build's
  entry on top of a stale `0.0.post0 unstable` placeholder;
- a plain `dpkg-buildpackage`, run without the version script, fails for
  want of a changelog instead of quietly building a package with the
  placeholder's version.

**Set A keeps its committed `debian/changelog`**: a repository whose
`debian/` came from Debian keeps Debian's entries, and the build's entry
goes on top of them.

**Epochs** (`2:`) are never introduced, except to recover from a version
that sorts wrongly. Each one is a recorded exception.

## The shared actions

- Publishing is **only** through
  `mithro/apt-repo-action/.github/workflows/publish-apt.yml@main`:
  - at `@main`, not a commit or tag, so every repository runs the same
    publisher;
  - no repository indexes, signs or deploys an apt repository itself.
- The build and the version are the shared `mithro/apt-repo-action`
  pieces: the reusable `build-deb.yml` workflow (see
  [Workflows](#workflows)), or, in a repository's own build job, the
  `build-deb/` action it runs; and the shared version script. A repository
  doesn't carry its own copy of `deb-version.py`.
- **`build-deb`** (`uses: mithro/apt-repo-action/build-deb@main`, as the
  `Build` step):
  - builds in `debian:<suite>`, or for `raspbian-<codename>` in a Raspbian
    root bootstrapped from archive.raspbian.org (see
    [`build-deb/raspbian/`](../build-deb/raspbian/README.md)), installing the build dependencies from
    `debian/control`, after adding the suite's
    [dependency repositories](#dependency-repositories);
  - in a Raspbian root (trixie on), apt accepts signing keys bound with
    SHA-1 self-signatures, as Raspbian's own key is; apt can't limit that
    to one source, so it applies to a dependency repository declared for a
    `raspbian-<codename>` suite too. Signatures over repository data still
    need SHA-2, and nothing built carries the setting (see
    [What the override covers](../build-deb/raspbian/README.md#what-the-override-covers));
  - takes `arch: all` for a repository whose packages are all
    `Architecture: all`: one build per suite, on the runner's own
    architecture;
  - stamps the version with the shared
    [`scripts/deb-version.py`](../scripts/deb-version.py), passing it the
    suite and, on a pull request, its number. The script reads the source
    name and maintainer from `debian/control`.
  - builds a patch series in the fetched project's tree: `source-dir` is
    that tree (with its rendered `debian/`), `version-tree` is this
    repository's checkout (usually `.`), whose tags and commits give
    `X.Y.postN` and the changelog's commit, and `version-args` carries the
    rest:

    ```yaml
          - name: Build
            uses: mithro/apt-repo-action/build-deb@main
            with:
              suite: ${{ matrix.suite }}
              arch: ${{ matrix.arch }}
              source-dir: build/src/qemu        # the fetched tree, debian/ added
              version-tree: .
              version-args: --owner-tag fpgasonline --upstream-dir . --epoch 2
    ```

    Paths in `version-args` are relative to `source-dir`.
- **The shared version script implements Set B, its patch series form and
  the epoch.** The Set A and backport forms are still to come
  (compliance-plan.md, section 3). Until then a repository that still has its
  own `packaging/deb-version.py` keeps it, and `build-deb` runs it, with a
  warning. A Set B repository deletes its own copy, in the commit that moves
  it to `build-deb` (see [below](#moving-a-repository-to-the-shared-build)).
- **A build that doesn't go through `build-deb`** gets the same version from
  `mithro/apt-repo-action/deb-version@main`, as its `version` output: a patch
  series whose own job renders the version into its templates, or an `nfpm`
  build. It takes `suite`, `source-dir` (the checkout, with full history) and
  `version-args`, and adds `~pr<P>` itself on a pull request.
- **`publish-apt.yml` refuses** to publish from a pull request, or from any
  ref but the default branch, whatever the caller's `if:` says.
- A repository that builds with `nfpm` instead of `dpkg-buildpackage` (Go
  static binaries) still follows every naming, version, suite and
  architecture rule here. Only the `Build` step differs:
  - the version is the `deb-version` action's output, passed to nfpm as
    `version: ${VERSION}` with `version_schema: none` (without it nfpm
    rewrites the version as semver);
  - the binary is built once per architecture, but packaged once per suite,
    since `~deb<R>` makes each suite's version different. Packaging is
    seconds; uploading one `.deb` to every suite would give every suite the
    same version, and sid's would no longer sort above the others;
  - CI no longer creates a `vX.Y` tag per release (Set B's tags are made by
    hand), so versions go on as `.post<N>` after the last tag.

### Moving a repository to the shared build

A repository with its own version script moves to `build-deb` in **one
commit**, which:
- changes the `Build` step to `uses: mithro/apt-repo-action/build-deb@main`;
- deletes `packaging/deb-version.py`;
- deletes any local build script the old `Build` step ran (nfsroot-watchdog's
  `packaging/ci-build.sh`), and any local test of the old version script.

That commit MUST NOT be split. `build-deb`'s default, `version-script: auto`,
runs the repository's own `packaging/deb-version.py` whenever the file
exists, with only `--write-changelog`: no suite, no pull request number. So a
commit that switches the `Build` step but keeps the script builds with it,
and fails if the script requires `--suite`. A commit that deletes the
scripts first leaves the old `Build` step without the scripts it runs.

`build-deb` doesn't pass `--suite` or `--pr` to a repository's own script on
purpose: tmux's and scanbd's accept only `--write-changelog`, and would fail.

Nor may the first shared build be of a tagged commit. A repository whose
last published version is a bare tag version (`0.24`, as the Go
repositories' CI published) would get `0.24~deb13` from a build of that same
commit, which sorts *below* `0.24`. The migration commit is a later commit,
so it gets `0.24.post1~deb13`, which sorts above: just don't tag it.

A Set B repository also deletes its committed `debian/changelog` and adds
`debian/changelog` to `.gitignore` ([The changelog](#the-changelog)). It
SHOULD do so in that same commit, but a separate commit, before or after,
builds too:
- the shared script works with or without a committed changelog;
- the repositories' own scripts write the whole file rather than adding to
  it, so they don't need one either. (sensors2mqtt's reads it only when
  `setuptools_scm` is missing, and its build dependencies install it.)

Doing it in the same commit means no published package carries the
placeholder underneath the build's entry: the shared script keeps a
committed changelog, while the old scripts replaced it.

To check it worked, look at any `build-deb` job of that commit:
- the `Build` step's environment shows `VERSION_MODE: shared` (`script`
  means the repository's own script ran);
- the run has no "using this repository's own packaging/deb-version.py"
  warning annotation. The same text also appears in the printed source of
  the `Check the inputs` step; that doesn't count;
- the version `dpkg-parsechangelog` prints ends in `~deb<R>` (nothing for
  sid), then `~pr<P>` on a pull request.

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
  - `build-deb` runs it after the build and shows each error and warning as
    a warning annotation, and all of them in the job summary.
  - Its `lintian` input is `warn` by default: lintian's findings never fail
    the build. `error` makes a lintian error fail the build, for a
    repository that has fixed its errors and wants to keep them fixed. `off`
    is for a build whose packages are checked elsewhere, and needs a reason
    like any other exception.
  - **lintian not being able to run fails the build**, in `warn` as well as
    `error`: the image can't be pulled, apt can't install lintian, or
    lintian or the report crashes. A build that says it was checked must
    have been. apt retries each download three times first, so a brief
    mirror failure doesn't fail it.
  - It runs in `debian:<codename>` on the runner's own architecture, not
    under QEMU, whatever the build's architecture.

## GitHub Releases

Optional. A repository that also publishes its builds as GitHub Releases:
- tags each one `build-<version>`, never `v*` (which is for
  [Set B releases](#set-b-our-code));
- names each `.deb` asset `<suite>_<file>.deb`, since every suite's build
  has the same filename;
- names each asset the way GitHub stores it, and lists those names in
  `SHA256SUMS`. GitHub "renames asset filenames that have special
  characters, non-alphanumeric characters, and leading or trailing
  periods" (REST API, "Upload a release asset"): `~` becomes `.`, so
  `trixie_claude-teleport_0.24.post5~deb13_amd64.deb` is stored as
  `trixie_claude-teleport_0.24.post5.deb13_amd64.deb`, while `+ . _ -` are
  kept. So every character but letters, digits and `. _ + -` becomes a dot
  before `SHA256SUMS` is written, and `sha256sum -c SHA256SUMS` passes on
  the downloaded assets. The Go repositories do this in
  `packaging/release-assets.sh`, and check it on every build;
- is made after `publish-apt`, which the release job `needs`: a build whose
  apt repository didn't deploy isn't released, and a re-run does both;
- includes everything built, debug symbols too. The apt repository is the
  convenient way to install; the release is the complete record.

## Documentation

- **README.md** (Set B, mirrors) or **packaging/README.md** (Set A) has an
  `## Install` section (that exact heading) with:
  - the setup block from [conventions.md](conventions.md#one-setup), once,
    for one named suite, with the real site URL;
  - a sentence telling the reader to put their suite in place of that one,
    naming every suite the repository publishes;
  - the key's fingerprint.

  `PKG-DOCS` checks each of these against the live site and key.
- **A package that needs a dependency repository** says so there too: the
  `## Install` section gives the setup for each
  [dependency repository](#dependency-repositories), for the suites it is
  declared for, before its own. For one of ours that is the same setup
  block with its name and site.
- `packaging/apt-intro.html` is optional: prose for the index page.
- The GitHub repository description says what the packages are. For Set A
  it names the upstream: "tmux, with …, packaged for Debian".

## Repository settings

- The default branch is `main` (Set B) or `packaging` (Set A, mirrors).
- Pages is built from GitHub Actions, with HTTPS enforced.
- The signing key is the repository secret `APT_GPG_PRIVATE_KEY`, the
  repository's own key ([conventions.md](conventions.md#one-signing-setup)).
- The `github-pages` environment allows deployments from the default branch
  only.

## The declaration

Every packaging repository has `.github/apt-packaging.toml` on its default
branch. It says what kind of repository it is, gives the reason for every
exception, and names the repository's
[dependency repositories](#dependency-repositories).
`scripts/apt-compliance.py` reads it, and `build-deb` reads its
`[[depends]]`; nothing else about a repository is recorded outside the
repository.

```toml
kind = "A"                  # "A": someone else's code, on GitHub; "B": ours;
                            # "mirror": someone else's, copied exactly;
                            # "aggregate": collects packages built elsewhere
variant = "backport"        # optional: "backport" (A) or "patch-series" (B)
upstream = "https://github.com/tmux/tmux"      # Set A; a mirror's: the git URL copied
architectures = "any"       # "any" (the default set), "all", or a list: ["arm64"]
suites = "default"          # "default", or the full list:
                            # ["bookworm", "trixie", "forky", "sid"]

[exceptions]                # rule ID = reason, for every rule not followed
PKG-SUITES = "fpgas.online uses it"

[[depends]]                 # optional, one per dependency repository:
repo = "mithro/paho-mqtt-bookworm"             # see "Dependency repositories"
suites = ["bookworm"]
reason = "python3-paho-mqtt (>= 2) is not in bookworm"
```

- `architectures` and `suites` other than the defaults need an entry under
  `[exceptions]` (`PKG-ARCH`, `PKG-SUITES`) saying why.
- A default `suites` adds the `raspbian-<codename>` suites when `armhf` is
  built. A listed `suites` is taken exactly.
- The rule IDs are the ones in
  [compliance-plan.md](compliance-plan.md#2-the-checker-scriptsapt-compliancepy).

A **mirror** also has a `[mirror]` table, which the sync and the checker read:

```toml
kind = "mirror"
upstream = "https://git.m-labs.hk/M-Labs/migen.git"   # what is copied (git ls-remote-able)

[mirror]
build = "master"                                  # the branch the package is built from
ours = ["github-master", "legacy", "experimental"]  # our branches besides packaging: never synced
tags = "[0-9]*"                                   # optional: upstream's version tags ("v[0-9]*"...)

[[mirror.patches]]                                # optional: our patches, in order (see above)
branch = "patches/streaming-axfr"
commit = "4f1c2d9e…"
```

### Dependency repositories

A package that needs something Debian doesn't have for a suite, to build
or to install, names the apt repository it comes from, one `[[depends]]`
table each. There are two forms.

**One of ours**, any repository that follows
[conventions.md](conventions.md), by its GitHub name:

```toml
[[depends]]
repo = "mithro/paho-mqtt-bookworm"
suites = ["bookworm"]       # optional: only these suites; left out, every suite
reason = "python3-paho-mqtt (>= 2) is not in bookworm"
```

- Its site is what GitHub reports for it (the Pages API's `html_url`,
  read with the workflow's token), so a custom domain needs nothing here.
  It MUST be `https://` (Pages enforcing HTTPS, as REPO-PAGES requires):
  the key is fetched from it.
- The source is `<site>/<suite>/ ./`, `<suite>` being the suite being
  built, and the key `<site>/<name>.gpg`, installed as
  `/etc/apt/keyrings/<name>.gpg`: the [one setup](conventions.md#one-setup).
- A repository of ours MUST use this form, so its URL and key are never
  written down twice.

**Anyone else's**, spelled out:

```toml
[[depends]]
name = "example"                      # the keyring and sources file: /etc/apt/keyrings/example.gpg
url = "https://example.org/debian"
suite = "{codename}"                  # the distribution; "./" for a flat repository
components = ["main"]                 # left out for a flat repository
key = "https://example.org/key.asc"   # binary or armoured; https
suites = ["trixie", "forky"]
reason = "libexample 3 is not in Debian"
```

- `{suite}` in `url` or `suite` is the suite being built
  (`raspbian-trixie`), `{codename}` its Debian codename (`trixie`). A flat
  repository with one directory per suite, like ours, is
  `url = "https://example.org/{suite}/"`, `suite = "./"`.
- The key MUST be `https://`: it is what apt trusts. (`http://` is
  accepted only for a loopback or private address, for the self-test's
  throwaway repository.) An armoured key is dearmoured.

For both:
- **`reason`** is required, as for an exception.
- **`suites`** names only suites from [Suites](#suites).
- Each source is added with `signed-by=` its own keyring. Never
  `[trusted=yes]`.
- The build fails, rather than building without it, if a dependency
  repository's key can't be fetched or isn't an OpenPGP public key, if its
  Release can't be fetched, or if apt can't verify its signature with that
  key.
- [`scripts/apt-sources.py`](../scripts/apt-sources.py) does all of this,
  for `build-deb` and for [install tests](#builds). `check` validates a
  declaration; `write --suite <suite> --dest <dir>` resolves it, fetches
  the keys and writes `<dir>/install.sh`, which adds them in any Debian
  container with nothing but apt.

The exceptions agreed on 2026-09-25, which each repository's declaration
should carry:

| repository | declaration |
|---|---|
| fpgas-online/apt | `kind = "aggregate"`, `architectures = "all"`, `suites = ["bookworm", "trixie", "forky", "sid"]`, `PKG-SUITES = "fpgas.online uses it"` |
| fpgas-online/fpgas.online-fpga-tools | `kind = "B"`, `variant = "patch-series"`, `architectures = ["arm64", "armhf"]`, bookworm added to the suites; `PKG-ARCH = "hardware-specific: Raspberry Pi 5 (RP1 PIO JTAG)"`, `PKG-SUITES = "fpgas.online uses it; NeTV2"`, `PKG-NODATES = "libpio: Raspberry Pi's piolib has no tags or versions"` |
| fpgas-online/nfsroot-watchdog | `kind = "B"`, `architectures = "all"`, bookworm added; `PKG-SUITES = "fpgas.online uses it"` |
| fpgas-online/rpi-qemu | `kind = "B"`, `variant = "patch-series"`; `PKG-VERSION = "epoch 2: recovers from an earlier version scheme"` |
| mithro/paho-mqtt-bookworm | `kind = "A"`, `variant = "backport"`, `architectures = "all"`, `suites = ["bookworm"]`; `PKG-SUITES = "backport: paho-mqtt 2.x for bookworm, needed by sensors2mqtt there"` |
| mithro/python-netgear-switch-library | `kind = "B"`, `architectures = "all"`, bookworm added; `PKG-SUITES = "fpgas.online uses it"` |
| mithro/rpi-hwid | `kind = "B"`, `architectures = "all"`, bookworm added; `PKG-SUITES = "NeTV2 (the rpi5-netv2 host)"` |
| mithro/scanbd | `kind = "A"`; `PKG-VERSION = "epoch 1: the count-based version sorts below the published 1.5.1+welland4"` |
| mithro/sensors2mqtt | `kind = "B"`, `architectures = "all"`, bookworm added; `PKG-SUITES = "fpgas.online uses it"`; `[[depends]]` `repo = "mithro/paho-mqtt-bookworm"`, `suites = ["bookworm"]` |
| mithro/ten64-microcontroller-utility | `kind = "A"`, `architectures = ["arm64"]`; `PKG-ARCH = "hardware-specific: Traverse Ten64"` |

Every other repository declares only its `kind` (and `upstream` or
`architectures = "all"`).
