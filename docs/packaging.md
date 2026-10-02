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
  - `build-deb`, given generated patches (`debian/patches/.generated`),
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
  - A patch series that applies its patches as a `3.0 (quilt)` series,
    writing `debian/patches/series` into the fetched tree, MUST also write
    `debian/patches/.generated` beside it. `build-deb` then applies the
    series itself and fails unless every patch applied: `dpkg-source
    --before-build` alone skips the whole series, exits 0, and builds the
    fetched project unpatched when the first patch no longer applies (a pin
    bump that changed the code it touches). It can't tell that from a tree
    whose patches are already applied, so only what wrote the series can
    say. One that applies its patches itself, failing on any that doesn't
    apply (`git apply`, `patch` with its exit status checked), needs none.

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
  in trixie). Such a dependency has a version of its own (see
  [Versions](#set-b)) and Debian's name (see
  [Package contents](#package-contents)).

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
  - **An end-to-end test that installs tools of its own** (an SSH server
    to test against, a client, a stock library to compare with) proves
    what a user gets only if the tools change nothing ours run with. So,
    in its container:
    1. `apt-get dist-upgrade` from the suite alone, so the container is the
       suite as a user of it has it (an image can lag its archive);
    2. install ours, from the suite alone (it must succeed), then
       `scripts/install-guard.sh save <file>`: every package and version
       now installed, ours and their whole closure;
    3. install the tools, then `scripts/install-guard.sh check <file>`,
       which fails, listing them, if any of those changed version or went.
       Packages the tools add are fine.

    paramiko-insecure's `packaging/e2e.sh` does the same check. When a tool doesn't
    install from a suite alone, see the Raspbian staging notes under
    [Suites](#suites);

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
- **Raspbian builds every release in `<codename>-staging`** and copies it
  into `<codename>` in batches, so a testing codename can be half-copied
  for weeks. From 2026-08, raspbian `forky` carried about 600 packages
  rebuilt for perl 5.42 while its own perl was still 5.40: nothing that
  build-depends on texinfo or a perl XS module could install its build
  dependencies from `forky` alone. A `raspbian-*` suite is still built
  then, never dropped:
  - `build-deb` installs the build dependencies from `<codename>` first,
    and only if that fails adds `<codename>-staging` (signed with the same
    archive key) and tries again
    ([`build-deb/raspbian/build-dep.sh`](../build-deb/raspbian/build-dep.sh));
  - Raspbian's builders also lag behind source uploads: on 2026-09-29,
    forky-staging had 920 source packages newer than their armhf binary
    (91 of them Rust crates), and an old binary can be what another package
    Breaks. While the build dependencies still don't install, `build-deb`
    rebuilds, from Raspbian's own signed sources, the packages apt's message
    names whose source is newer than their binary (with the `nocheck`
    profile), and tries again. `raspbian-rebuild` names sources to rebuild
    up front;
  - what came from staging or was rebuilt is listed in the step summary, a
    warning and `build-deb`'s `from-staging` and `rebuilt` outputs;
  - staging and the rebuilt packages are only ever *installed* in the
    build's own container, never in the image. But what the build
    compiles from them can end up in the published package: a statically
    linked Rust crate, headers, generated code. That code is Raspbian's
    own (from its signed archive and sources; a rebuild is built with
    `nocheck`, so its tests don't run), and every such package is listed
    in the step summary and the `from-staging` / `rebuilt` outputs;
  - the install test runs in a clean container of the image, which has
    `<codename>` alone, so a package whose *run-time* dependencies only
    staging or a rebuild has (a library version that `dh_shlibdeps` took
    from staging, say) fails it and isn't published. That only covers
    run-time dependencies, and only where an install test runs. Such a
    package waits for Raspbian to copy staging over, or for its build
    dependencies to be pinned below staging's versions;
  - `build-deb/raspbian/staging.sh` (`add <codename> <notes>
    [--sources]`, `report <notes>`) is how `build-dep.sh` adds staging,
    verified with the same pinned key, and lists what came from it;
  - **a test's own tools** on a half-copied codename (see the end-to-end
    test under [Builds](#builds)) come from the suite too, not from
    staging, whenever that's possible: staging's newer libraries are
    exactly what would change what ours run with. When a tool's own
    package can't install because of a dependency chain the suite can't
    satisfy, but the tool itself runs from the suite's files, unpack the
    suite's package without its maintainer scripts (`apt-get download`,
    `dpkg --unpack`, which checks no Depends), install what it runs with
    from the suite, do by hand what its postinst would (a system user, say),
    and check `ldd` finds every library. paramiko-insecure's
    raspbian-forky test does this for `openssh-server`, whose `ucf` ->
    `libtext-wrapi18n-perl` -> `libtext-charwidth-perl` needs perl 5.42
    (only in forky-staging); installing it from staging upgraded the
    `perl-base` ours run with (`python3` -> `tzdata` -> `debconf` ->
    `perl-base`), which `install-guard.sh check` caught. Use
    `staging.sh add` for a tool only when that is impossible, and only
    with `install-guard.sh check` passing: nothing ours run with changed;
- A repository whose packages are all `Architecture: all` publishes only the
  Debian suites. Raspbian hosts use the Debian suite of the same codename:
  the packages are the same files.
  - **Unless it bundles an architecture-dependent dependency**
    ([Bundling a dependency repository](#bundling-a-dependency-repository)):
    the Debian suite's armhf copy of that dependency is Debian's ARMv7
    build, which ARMv6 Pis (Zero, Pi 1) can't run. Such a repository MAY
    list `raspbian-<codename>` (with `<codename>` itself) wherever a
    `bundle` dependency applies, so an ARMv6 host still adds one
    repository. That suite holds our packages as built for `<codename>`,
    the same files (the `~deb<R>` suffix is the codename's), plus the
    dependency's `raspbian-<codename>` packages; there is no separate
    Raspbian build, and it is install-tested as armhf in the Raspbian root.
    paramiko-insecure does this for python3-cryptography-insecure.
  - The dependency repository must publish `raspbian-<codename>` itself:
    each suite bundles from the dependency's suite of the same name. If it
    doesn't, the publish fails (its `raspbian-<codename>/InRelease` can't
    be fetched), rather than serving Raspbian hosts Debian's ARMv7 build.
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
- **A restricted set** is allowed for exactly two reasons, and the
  `PKG-ARCH` exception names which:
  - **Hardware-specific** packages: code that can only run on one piece of
    hardware. The set is that hardware's architectures, and the exception
    names the hardware:
    - Traverse Ten64 (NXP LS1088A): `arm64`;
    - Raspberry Pi: `arm64`, `armhf`, and the `raspbian-*` suites.
  - **Upstream restricts it** (Set A and mirrors only): upstream's own
    packaging lists fewer architectures, so the others can't be built
    from upstream's code. The set is upstream's, and the exception names
    where upstream says so. usdr-lib: upstream's `debian/control` gives
    every compiled package `Architecture: amd64 arm64`. Building for more
    means carrying our own porting changes, which is a separate decision.
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
commit produces the same version. The one exception is a
[dependency built from someone else's release](#set-b), whose version
changes only when what it is built from does.

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
build's entry; the build fails if that changelog is for another version
than the pin (the code and the version would disagree). A Debian binNMU of
the same source (`49.0.0-2+b1`) sorts below ours (`b` before `w`), so it
doesn't replace our build and needs no new pin.

A **dependency built from someone else's release** (a step of the build, see
[Workflows](#workflows): ntrip-rtcm3-to-rtcm2p3's pyrtcm and pynmeagps) is a
source package of its own, and someone else's code. It takes
[Set A](#set-a)'s form at a release, not the repository's own version:

```
<upstream>-0+<owner-tag><M>[~deb<R>][~pr<P>]
```

for example `1.1.7-0+welland4~deb13`.

- **`<upstream>`** is the release that is built, normalised as Set A's tags
  are. It MUST be a release, with revision `0`: that sorts below Debian's
  first revision (`-1`), so Debian's package of the same release replaces
  ours. A `+git<N>` base or a Debian revision would sort above Debian's.
  Anything more (an unreleased commit, patches, Debian's own `debian/`) is a
  Set A repository of its own.
- **`<M>`** counts the commits that changed the script that builds it
  (`git rev-list --count HEAD -- <script>`), so a change to the packaging is
  a new version. Everything of ours that shapes the package MUST live in
  that script: which release will do, the build dependencies, the `debian/`
  it writes. The step MUST fail in a shallow clone, where the count would be
  1, and a renamed script MUST carry the old count on.
- **It is built only for the suites whose Debian archive lacks the package**
  at the version needed. Where Debian has it, the build MUST NOT make one:
  ours would shadow Debian's. The build asks the suite's own apt, so this
  follows Debian's archive by itself.
- **It is republished at the same version** until the script or the upstream
  release changes. A push that changes neither builds the same package
  again, as a later build of the same commit does (the changelog's date is
  the script's last commit's). A push that changes either MUST produce a
  version greater than everything already published for it in that suite: a
  new release raises `<upstream>`, a new commit to the script raises `<M>`.
- `PKG-VERSION` tells such a package from the repository's own by the
  `Source:` of its stanza in the published index: a source that isn't the
  one in the repository's root `debian/control`. So it MUST be built as its
  own source package.

An **epoch** (`2:`) is only for a repository recovering from an earlier
version scheme, and is declared as a `PKG-VERSION` exception (rpi-qemu).

### Set A

```
<base>+<owner-tag><M>[~deb<R>][~pr<P>]
```

| part | from |
|---|---|
| `<base>` | 1. Debian's version, when `debian/` came from Debian and is for the release `upstream` is exactly at: `1.1.2-7`<br>2. otherwise the upstream release tag, when `upstream` is exactly at one: `2.93-0`<br>3. otherwise `<tag>+git<N>.g<sha7>-0`, with `N` = upstream commits since that tag and `sha7` the upstream commit<br>4. upstream has no tags at all: `0.0+git<N>.g<sha7>-0`, with `N` = all upstream commits |
| `<owner-tag>` | `welland` for `mithro/*`, `fpgasonline` for `fpgas-online/*` |
| `<M>` | commits on `packaging` that aren't on `upstream`: `git rev-list --count upstream..packaging` |

Upstream tags are normalised to a Debian upstream version:
- a leading `v` or project-name prefix goes (`v2.93` → `2.93`,
  `netplan-1.1.2` → `1.1.2`, `my-project-1.2` → `1.2`);
- `_` becomes `.` (`RELEASE_7_5` → `7.5`);
- a pre-release gets a `~`, so it sorts below its release, as
  [a mirror's](#mirrors) does: a `-` becomes `~` (`3.8-rc3` → `3.8~rc3`), and
  `rc`, `test`, `alpha`, `beta` or `pre` (in any case) straight after a digit
  or a dot gets one put before it (`v2.94rc1` → `2.94~rc1`, `v2.94test1` →
  `2.94~test1`, `1.0.rc1` → `1.0~rc1`). Other suffixes are left as they are:
  tmux's `3.5a` is a later release than `3.5`;
- a `-` before a digit fails the build: a date (`release-2024-01-15`) or a
  revision (`1.2-3`) can't be told from a pre-release, and as `2024~01~15`
  it would sort below `2024`. So does a tag that still isn't a Debian
  upstream version (`debian/2.90-1`). `--upstream-tag-match` picks the tags
  that are releases.

Between two pre-release words the order is dpkg's, the alphabet's: `alpha`,
`beta`, `pre`, `rc`, `test`. An upstream that makes its `test` releases
before its release candidates (dnsmasq) therefore sorts wrongly between
them until the release itself.

The shared script makes it, given the owner's tag and the branch that holds
upstream's history: `scripts/deb-version.py --owner-tag <owner-tag>
--upstream-branch upstream`, which a repository passes as the build's
`version-args` (see [The shared actions](#the-shared-actions)):

```yaml
  build-deb:
    uses: mithro/apt-repo-action/.github/workflows/build-deb.yml@main
    with:
      version-args: --owner-tag welland --upstream-branch upstream
```

- **The upstream commit** is the one the build is made on: the merge base of
  the build commit and `upstream` (`origin/upstream` in a CI checkout, which
  has no local branches). So `upstream` moving on changes nothing until it is
  merged, and a pull request that merges it is versioned as the merge will
  be. `sha7` is the first seven digits of its id.
- **`N`** is `git describe --tags --long`'s count for that commit: the
  upstream commits since its nearest tag. `--upstream-tag-match <glob>`
  (in `version-args`) limits the tags to upstream's releases, when it has
  others (`debian/2.90-1`). A tag that doesn't normalise to a Debian
  upstream version fails the build.
- **No tag at all** gives base 4, with a warning: it is also what a checkout
  without upstream's tags looks like, and `0.0+git<N>` sorts below every
  release.
- **`M`** counts from the build commit (`upstream..HEAD`), so a pull
  request's count is the one its merge commit will have.
- **Debian's version** (1) is the top of the committed `debian/changelog`,
  and is the base only while it says what is built: `upstream` is exactly at
  a release (`N` = 0), and that changelog's version is `<release>-<revision>`
  for the same release. Ours then extends Debian's revision, and sorts above
  Debian's build of that release. Once upstream is merged past the release,
  the base is (3), which sorts above every Debian revision of the release
  and below Debian's next upstream version. smartmontools' `debian/` is
  Debian's 7.5-2, and it builds upstream's `main`:
  `7.5+git583.g06489e0-0+welland10~deb13`.
  - A top entry of ours on Debian's (`1.1.2-7+welland1`, committed) is read
    as Debian's `1.1.2-7`.
  - Debian's repack of the release counts as the release when it is marked
    `+dfsg…` or `+ds…` (`2.93+dfsg-1`), which sort below the `+git<N>` that
    follows. Any other suffix on the release (`2.0+repack-1`,
    `2.0+really1.9-1`, `2.0.ds1-1`, `2.0+git20240101-1`) sorts above
    `2.0+git<N>`, so Debian's package would replace ours: the build fails,
    at the release and after it.
  - A committed version with an epoch needs the same `--epoch` (a declared
    `PKG-VERSION` exception), whatever the base, or the build fails:
    without it every build sorts below Debian's.
- **An upstream whose release tags aren't on the branch we build** has its
  releases named by commit subject instead. smartmontools' history came from
  svn: its `RELEASE_7_5` tag is on a commit off `main`, where `git describe`
  can't find it from `main`, and `main` has its own `Release 7.5
  RELEASE_7_5` commit. [The declaration](#the-declaration) gives the pattern:

  ```toml
  [version]
  release-subject = '^Release (\d+(?:\.\d+)+) RELEASE_\d+(?:_\d+)+$'
  ```

  The release is then the nearest commit on upstream's own line (the first
  parents of the upstream commit) whose subject matches: a Python regular
  expression, searched for anywhere in the subject unless anchored. A
  maintenance release merged into the branch later (7.4.1, after 7.5) is
  not on that line, so it never takes the version back; nor is
  smartmontools' second `Release 7.5 RELEASE_7_5` commit, the one its svn
  tag was made from. `N` is every commit since the release, merged ones too,
  and the pattern's one group (everything it matched, without a group) is
  the release, normalised as a tag is: `7.5`, or `RELEASE_7_5`. With a
  pattern, tags aren't read, and a pattern that matches no commit fails the
  build: it never falls back to `0.0`. `--upstream-release-subject` gives
  the pattern on the command line instead, but not through `version-args`
  when it has a space, since those are split at spaces: the declaration is
  the place for it.
- **A repository declared `kind = "A"` never gets the Set B form.** Without
  `--upstream-branch` the script fails, instead of making `0.0.post<N>`
  from our own tags, which a Set A repository doesn't have. `PKG-SHARED`
  checks the workflow for it too.

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
    [dependency repositories](#dependency-repositories); a Raspbian suite
    whose build dependencies it alone can't satisfy falls back to
    `<codename>-staging`, which [Suites](#suites) describes;
  - on an arm64 runner, runs Raspbian's ARMv6 memory barriers in hardware
    (`abi.cp15_barrier = 2`): under the kernel's default emulation,
    Raspbian trixie's and forky's rustc never finish (see
    [ARMv6 memory barriers](../build-deb/raspbian/README.md#armv6-memory-barriers-on-an-arm64-runner));
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
  - gives a Set A repository its version with `version-args: --owner-tag
    <owner-tag> --upstream-branch upstream` ([Set A](#set-a)); the reusable
    `build-deb.yml` passes its own `version-args` input on. The checkout
    needs the `upstream` branch: `fetch-depth: 0` fetches every branch.
- **The shared version script implements Set B, its patch series form,
  Set A and the epoch.** Set A is asked for with `version-args`
  (`--owner-tag <owner-tag> --upstream-branch upstream`, see
  [Set A](#set-a)); without them the version is Set B's, which the script
  refuses to make for a repository declared `kind = "A"`. The backport form
  is still to come (compliance-plan.md, section 3). A repository that still
  has its own `packaging/deb-version.py` keeps it until it is moved, and
  `build-deb` runs it, with a warning. A Set A or Set B repository deletes
  its own copy in the commit that moves it to the shared version (see
  [below](#moving-a-repository-to-the-shared-build)).
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

**A Set A repository** adds `version-args: --owner-tag <owner-tag>
--upstream-branch upstream` in that same commit (and `[version]
release-subject` to its declaration, if upstream's release tags aren't on the
built branch). Without them the build fails: the shared script doesn't make
the Set B version, from our own `vX.Y` tags, for a repository declared
Set A. Before merging, compare the pull request's preview
version with what the suite publishes (`dpkg --compare-versions`): a
repository whose own script made versions another way (a date, a hand-set
`+welland<M>`) may need the new version to be raised, by an epoch under a
`PKG-VERSION` exception, as scanbd's is.

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
  - A dependency a Set B repository builds from someone else's release (see
    [Versions](#set-b)) is the exception: it MUST have the name Debian gives
    the package, or would give it (`python3-pynmeagps`). The two never meet
    in one suite, since it isn't built where Debian has the package, and on
    the upgrade to a suite that has it, Debian's package replaces ours.
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
  block with its name and site. A
  [bundled](#bundling-a-dependency-repository) one needs none: users get
  its packages from ours.
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
owners = ["fpgas-online"]   # optional: other GitHub owners whose repositories
                            # are ours too (see "Bundling a dependency repository")

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

A **Set A** repository whose upstream's release tags aren't in the history of
the branch it builds also has a `[version]` table, which the shared version
script and the checker read (see [Set A](#set-a) under Versions):

```toml
kind = "A"
upstream = "https://github.com/smartmontools/smartmontools"

[version]
# upstream's releases are main's "Release 7.5 RELEASE_7_5" commits: its
# RELEASE_7_5 tags (from svn) are on commits off main
release-subject = '^Release (\d+(?:\.\d+)+) RELEASE_\d+(?:_\d+)+$'
```

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

#### Bundling a dependency repository

Without more, a user adds every dependency repository as well as ours.
`bundle` asks publish-apt to serve the dependency repository's packages
that ours need from our own suites instead, signed with our key, so a user
adds only ours:

```toml
[[depends]]
repo = "mithro/paho-mqtt-bookworm"
suites = ["bookworm"]
bundle = true
reason = "python3-paho-mqtt (>= 2) is not in bookworm"
```

- **Whose.** Bundling re-signs the dependency's packages with our key, so
  our users trust them as ours. `bundle = true` is only for a `repo` of
  one of our owners: the declaring repository's own, and any GitHub owners
  the declaration lists in `owners`, for one person or project spread over
  several users and organisations:

  ```toml
  owners = ["fpgas-online"]      # in mithro/sensors2mqtt: fpgas-online's repositories are ours too
  ```

  Anything else, a `repo` of another owner included, must say
  `bundle = "third-party"`, only when we vouch for that repository; an
  explicit (non-`repo`) one must also be a flat repository. The list is in
  the declaration, not a workflow input, because everything that applies
  the rule reads the declaration already: publish-apt's bundling, the
  install test, `refresh-bundled.yml` and PKG-DEPENDS, which applies the
  same rule. Nothing in apt-repo-action names any owner.
- **Which packages.** Each `Depends` and `Pre-Depends` relation of ours
  that the dependency repository can satisfy (the package at a version the
  relation allows, or a `Provides`; a versioned relation only by a
  versioned `Provides`), and what those need from it in turn. What it
  doesn't have is left to Debian. An alternative Debian would satisfy may
  be bundled too: whether Debian has it can't be known without the
  archive, and what a dependency repository has is normally why it was
  declared. A relation without alternatives on a package the dependency
  repository has, none of whose versions satisfies it, fails the publish.
- **Which architectures.** A package needed by an `Architecture: all`
  package of ours is bundled for every architecture the dependency
  repository has it for, since ours installs anywhere; one needed only by
  architecture-dependent packages, for their architectures. The suite's
  `Architectures` counts them. In a `raspbian-<codename>` suite that comes
  from the dependency's `raspbian-<codename>` suite, so an Architecture: all
  repository can serve ARMv6 hosts too (see [Suites](#suites)).
- **Which versions.** For each relation, the newest version that satisfies
  it when we publish (by `dpkg --compare-versions`), so several versions
  of one package if relations need them. keep-history keeps earlier ones
  as it does ours. A client that also has the dependency repository gets
  whichever version is higher, as apt always does, so nothing needs
  pinning.
- **Nothing unverified.** The dependency repository's `InRelease` must
  verify with its key (only the verified text is read), and, for one of
  ours, be for this suite. Its `Packages` must match the hash `InRelease`
  gives, and each `.deb` the `Size` and `SHA256` its `Packages` gives.
  Each `.deb`'s own control file must also say exactly what its stanza
  does (`Package`, `Version`, `Architecture`, `Source`, `Multi-Arch`,
  `Essential` and its relations): our index is made from the files, so a
  file claiming to be another package, one of ours say, would otherwise
  enter it. If anything fails, an unreachable repository included, the
  publish fails:
  publishing without the dependency would leave our packages
  uninstallable for anyone using only our repository.
- **Marked.** Each bundled package's stanza in our signed `Packages` says
  `Bundled-From: <repository>` (`owner/name`, or the flat URL), and the
  index page shows it. The compliance rules about our own packages leave
  bundled ones out.
- **Building is unchanged.** `build-deb` still adds the dependency
  repository for the build. The [install test](#builds) offers the
  bundled packages to apt as a local source and adds only the other
  dependency repositories, so apt chooses among them (one of several
  alternatives or versions) as it would from the published repository,
  which proves that repository is enough. A repository with its own install
  test does the same:

  ```sh
  python3 <apt-repo-action>/scripts/bundle-depends.py fetch --suite "$SUITE" \
    --arch "$ARCH" --debs built-debs --dest bundled-debs --index
  python3 <apt-repo-action>/scripts/apt-sources.py write --unbundled \
    --suite "$SUITE" --dest apt-sources
  # in the container: sh apt-sources/install.sh
  #   echo "deb [trusted=yes] file:/bundled-debs ./" > /etc/apt/sources.list.d/bundled.list
  #   apt-get update; apt-get install ./built-debs/*.deb
  ```

  `trusted=yes` is for the throwaway test container only: every bundled
  file was already verified as above. Never in anything published.
- **The README** gives only our repository's setup: PKG-DOCS asks for no
  setup lines for a bundled dependency repository.
- **Keeping up.** A newer version in the dependency repository reaches our
  users at our next publish. To not wait for one, call
  [`refresh-bundled.yml`](../.github/workflows/refresh-bundled.yml) on a
  schedule: it runs the same selection over our live packages and the
  bundled repositories' indexes, and starts the build workflow only when
  that would bundle something our live site doesn't have, so most days
  nothing runs:

  ```yaml
  name: Refresh bundled packages
  on:
    schedule: [{cron: "23 4 * * *"}]
    workflow_dispatch:
  permissions:
    contents: read
    actions: write
  jobs:
    refresh:
      uses: mithro/apt-repo-action/.github/workflows/refresh-bundled.yml@main
      with:
        suites: "bookworm trixie forky sid"
  ```

  A dependency repository starting its dependents' builds instead would
  need a token able to start workflows in other repositories.
- [`scripts/bundle-depends.py`](../scripts/bundle-depends.py) does the
  copying (`fetch`) and the check (`stale`).

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
| mithro/usdr-lib (agreed 2026-09-29) | `kind = "A"`, `architectures = ["amd64", "arm64"]`; `PKG-ARCH = "upstream supports only amd64 and arm64: …"` (its `packaging/debian-bookworm/control`) |

Every other repository declares only its `kind` (and `upstream` or
`architectures = "all"`).
