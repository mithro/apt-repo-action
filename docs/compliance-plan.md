# Keeping every repository in compliance

How apt-repo-action brings every apt repository in line with
[packaging.md](packaging.md) and [conventions.md](conventions.md), and keeps
it there as conventions are added. The checker (section 2) exists; the rest
is a plan.

On 2026-09-25 no repository meets every rule. `scripts/apt-compliance.py`
finds 20 packaging repositories, and each fails between 7 and 17 of its
28 rules; `PKG-DECLARED` fails everywhere, since no repository has its
declaration yet. Apart from that, most failures are the same six rules,
each failing in 18 to 20 repositories:
- the workflow file's name;
- the concurrency block;
- building with the shared build and version script;
- the default suites (forky and Raspbian);
- the `~deb<R>` suffix;
- an install test.

Those are what shared code fixes best (section 3). The per-repository lists
are in the compliance report.

## Principles

1. **A rule that isn't checked by a program drifts.** Every rule gets an ID
   and a check, in the same pull request that adds the rule.
2. **Put the rule in shared code instead of in 20 copies.** The version
   script, the build matrix and the install test live in apt-repo-action,
   and every repository calls them at `@main`. Fixing it there fixes it
   everywhere.
3. **Exceptions are data.** Each repository declares its own, with a
   reason, in `.github/apt-packaging.toml`, and the checker reads that file.
   An undeclared difference is a failure, not a judgement call.
4. **New rules start as warnings.** A rule only fails builds once every
   repository passes it or has an exception, so adding a rule never breaks
   a publish.

## 1. Each repository declares itself

Nothing about a repository is kept outside it. Its
[`.github/apt-packaging.toml`](packaging.md#the-declaration) gives its kind
(Set A, mirror, Set B, aggregate), variant, upstream, and any non-default suites or
architectures, with a reason for each exception, keyed by rule ID. An
exception is reviewed in the repository's own pull request, like any other
change to its packaging.

Until a repository has its declaration, the checker infers its kind:
- **aggregate** if its workflows build nothing;
- **Set A** if it is a fork, has an `upstream` branch, carries commits by
  people outside the owner (or the organisation's members) from before the
  repository existed, or publishes `~bpo` or `+<owner-tag><M>` versions;
- **Set B** otherwise.

A mirror is never inferred: a GitHub fork with a `packaging` default branch
(migen) looks like Set A until its declaration says otherwise. Inferred
kinds fail `PKG-DECLARED`.

**Discovery** needs no list either. A repository is a packaging repository
when a workflow, on its default branch or on the branch its Pages site last
deployed from, calls apt-repo-action (`uses: <action-repo>/…`) or indexes an
apt repository itself (`dpkg-scanpackages`, `apt-ftparchive`, `reprepro`).
Comments don't count. A repository that stops publishing (as rp1-jtag did)
stops being found. A Pages site that still serves `<repo>.gpg` without such
a workflow is reported separately, as a *site without packaging*: something
still serves frozen packages there.

## 2. The checker: `scripts/apt-compliance.py`

One self-contained script (`uv run`, needs only `gh`). It knows what the
conventions mean and nothing about any particular owner; the owners, their
version tags and the maintainer are arguments:

```sh
uv run scripts/apt-compliance.py \
  --owner mithro=welland --owner fpgas-online=fpgasonline \
  --maintainer "Tim 'mithro' Ansell <me@mith.ro>" \
  --html report.html --markdown report.md --json report.json
```

It discovers the repositories, gathers facts from the GitHub API and the
live sites, checks each rule, and writes the tables and per-repository todo
lists (HTML), one checklist per repository (Markdown, the body of the
repository's issue in section 4), and everything as JSON.

- `--repo owner/name` (repeatable) checks only those repositories, with no
  scan of the owners' others: seconds, not minutes.
- `--local PATH` checks a checkout before it is pushed: its workflows,
  declaration, `debian/`, README and `.gitignore` as they would be
  committed (tracked files, and untracked ones git doesn't ignore). The
  branch, the history and the live site still come from GitHub. The
  repository is `--repo`, or the checkout's origin.

| ID | rule | how it's checked |
|---|---|---|
| PKG-DECLARED | the kind is declared, and the build accepts the declaration | `.github/apt-packaging.toml` parses, `build-deb.yml`'s `scripts/build-matrix.py` wouldn't refuse its `suites` and `architectures`, a mirror's `upstream` and `[mirror]` (`build`, `ours`, `tags`) are well-formed, and so is Set A's `[version]` (`release-subject`, a regular expression) |
| PKG-BRANCH | default branch `packaging` (A, mirror) or `main` (B), and it publishes | GitHub API, last Pages deployment |
| PKG-HISTORY | Set A carries upstream's history; a mirror's `packaging` shares none with the built branch | fork, or commits by others before the repository existed; a mirror: the compare API finds no common ancestor |
| PKG-UPSTREAM | Set A has an `upstream` branch; a mirror's built branch is upstream's | GitHub API; a mirror: `git ls-remote` of the declared `upstream` (unreachable fails), or upstream moved since the last sync, which succeeded in the last two days |
| PKG-SYNC | Set A has `sync-upstream.yml` (backport: a schedule); a mirror's is `Sync upstream`, scheduled, calls the shared `sync-mirror.yml@main`, grants `contents`/`actions` (and with patches `issues`) write, has a concurrency group that waits, and no tag ruleset refuses upstream's tags | files on the publishing branch; a mirror: the workflow and its jobs' `uses:`, and upstream's tags (`git ls-remote`; unlistable fails) against the active tag rulesets without a GitHub Actions bypass |
| PKG-PATCHES | a mirror's `[[mirror.patches]]` branches exist, each pin is its branch's tip and based on the built branch; no committed `debian/patches` | GitHub API: branches, compare |
| PKG-README | Set A has `packaging/README.md`; a mirror has `README.md` naming its upstream | file |
| PKG-DEBIAN | `debian/` at the root (patch series: `packaging/debian/<name>/`) | tree |
| PKG-CHANGELOG | Set B and mirrors commit no `debian/changelog` (nor a patch series' templates), and `.gitignore` lists it | tree, `.gitignore` |
| PKG-DEPENDS | each `[[depends]]` is well-formed, with a reason and known suites; each published suite a `bundle` one applies to holds packages bundled from it | the declaration, with `scripts/apt-sources.py`'s own validation; notes whether a `repo` is a packaging repository in the scan; live `Packages` (`Bundled-From:`) |
| PKG-WORKFLOW | `.github/workflows/deb.yml`, `name: Debian packages` | parse YAML |
| PKG-JOBS | jobs `test`, `build-deb`, `publish-apt`, `release` only | parse YAML |
| PKG-TRIGGERS | push to default + pull_request + workflow_dispatch; nothing else; every `workflow_run` job guarded against pull requests | parse YAML, every workflow |
| PKG-PREVIEW | pull requests build, never publish | YAML + the publish job's `if:` |
| PKG-CONCURRENCY | `deb-${{ github.ref }}`, cancelling pull requests only | parse YAML |
| PKG-PUBLISHER | `publish-apt.yml@main` | parse YAML |
| PKG-SHARED | shared build at `@main`: the reusable `build-deb.yml`, the `build-deb` action, or the `deb-version` action for an nfpm build or a patch series' own job; every use at `@main`; no local `deb-version.py` | YAML + tree |
| PKG-INSTALL-TEST | an `Install test` step that runs something, in the job that builds (or the shared `build-deb.yml`) | YAML |
| PKG-SUITES | default suites, or declared with a reason | live site, against the declaration read as `build-deb.yml` plans its builds |
| PKG-ARCH | default architectures per suite, or declared with a reason; nothing advertised without packages | live site, against the declaration read as `build-deb.yml` plans its builds |
| PKG-NODATES | no date in a version | live `Packages` |
| PKG-VERSION | version matches its kind's form; no epoch | live `Packages` |
| PKG-SUITE-SUFFIX | `~deb<R>` on every suite but sid | live `Packages` |
| PKG-DBGSYM | no `-dbgsym` over 10 MB in apt | live `Packages` |
| PKG-MAINTAINER | the expected `Maintainer:` | `debian/control` |
| PKG-DOCS | a `## Install` section (that exact heading) holding the setup block for one suite, the name of every published suite, the key's fingerprint (read from the live key) and each dependency repository's setup, except a bundled one's; nothing conventions.md forbids | README, live key |
| REPO-PAGES | Pages from Actions, HTTPS enforced | GitHub API |
| REPO-KEYS | `<repo>.gpg` binary, `<repo>.asc` armoured | live site |
| REPO-LAYOUT | flat signed suites, nothing at the root | live site |
| REPO-RELEASE | Origin = Label = `<repo>`, `Suite: stable`, `Codename: <suite>` | live `Release` |
| REPO-INDEX | the one setup for every suite, nothing forbidden | live index page |

Still to add:
- a **`--self`** mode for use inside a build: the static checks for the
  calling repository only (branch, names, triggers, declaration), in seconds;
- severities (`warn`/`error`) per rule, for section 5.

## 3. Move the rules into shared code

These do most of the work: once they exist, following the convention is
what a repository gets by default.

1. **`scripts/deb-version.py`**, one version script:
   - implements every form in packaging.md (Set A, Set B, backport, patch
     series), plus `~deb<R>` and `~pr<P>`;
   - has a test table, including every ordering in packaging.md;
   - replaces the 11 diverging copies of `packaging/deb-version.py`.

   Started: Set B, the patch series form, Set A, the epoch and both suffixes
   are done. `build-deb/` runs it (`version-tree`, `version-args` for a patch
   series), `deb-version/` gives the version to nfpm builds and to patch
   series with their own job, and `tests/test_deb_version.py` checks the
   ordering tables.
   - Set A is `version-args: --owner-tag <owner-tag> --upstream-branch
     upstream`: `<base>+<owner-tag><M>`, with all four bases in packaging.md
     (Debian's version, a release, commits after a release, an upstream
     without tags). An upstream whose release tags aren't on the built
     branch (smartmontools, imported from svn) declares the subject its
     release commits have, `[version] release-subject`. No Set A repository
     uses it yet: smartmontools is the first to move (issue #48), and the
     others still carry their own script.
   - The backport form is still to come.
2. **`.github/workflows/build-deb.yml`**, a reusable build workflow:
   - takes `suites`/`architectures` (default: the defaults);
   - runs the matrix on the right runners, with QEMU for foreign
     architectures;
   - builds Raspbian suites from a Raspbian root file system;
   - stamps the version, builds and runs the install test;
   - splits `-dbgsym` over 10 MB, runs lintian, and uploads artifacts with
     the fixed names.

   A repository's `deb.yml` shrinks to about 25 lines: triggers,
   concurrency, `uses: build-deb.yml`, `uses: publish-apt.yml`. `build-deb/`
   (the composite action) stays for repositories that need their own job
   around the build (nfpm, patch series).

   Done, except lintian: `.github/workflows/build-deb.yml` plans the matrix
   from the declaration (`scripts/build-matrix.py`), builds the Raspbian
   suites in an ARMv6 root (`build-deb/raspbian/`), runs armhf natively on
   the arm64 runners, keeps large `-dbgsym` out of apt, and install-tests
   each suite.
3. **`publish-apt.yml` enforces what can only be checked at publish time:**
   - refuses to publish from a pull request or any ref but the default
     branch (done);
   - refuses a version that isn't greater than what the suite already
     publishes (PKG-VERSION's "every push is newer");
   - refuses to advertise an architecture with no packages;
   - runs `apt-compliance.py --self` and prints its warnings, turning into
     errors per rule as described below.
4. **Scaffolding for new repositories**:
   - `scripts/new-repo.py --set B` writes `deb.yml`, `debian/` and the README
     install section;
   - `--set A --upstream <url>` forks or imports with history, creates
     `upstream` and `packaging`, and adds `sync-upstream.yml`.

   A new repository is compliant from its first commit.

## 4. Continuous checking

- **Nightly `compliance.yml` in apt-repo-action** runs
  `scripts/apt-compliance.py` over the owners named in the workflow (and on
  `workflow_dispatch`, with owners and maintainer as inputs). It exists, and
  so far only:
  - keeps `report.{html,md,json}` as the run's `compliance-report`
    artifact;
  - writes the Markdown checklists into the run's job summary.

  It changes nothing anywhere. The run's own token suffices: a scan makes
  about 250 API requests, and every packaging repository is public. That
  token sees only an organisation's *public* members, though, so in an
  organisation whose members are all private, an undeclared repository's
  commits by its own people look like upstream's and its kind is inferred
  as Set A. A declaration settles the kind, so this ends once every
  repository has one.

  Still to come:
  - publishing the report on apt-repo-action's own Pages site;
  - keeping **one issue per non-compliant repository**, in that
    repository, titled `apt conventions: N rules failing`. The body is the
    todo list as checkboxes, and it closes itself when the repository
    passes. Whether to do this is still to be decided.
- **Every build checks itself.** `build-deb.yml` runs `--self` first. A pull
  request that renames a job, drops a trigger or adds an unregistered suite
  gets a warning, or a failure once that rule is enforced, before it
  merges. This needs no per-repository setup, because every repository
  calls `build-deb.yml`.
- **apt-repo-action protects `main`**, since every repository runs it at
  `@main`:
  - `selftest.yml` is a required check;
  - a `canary.yml` builds and install-tests three real repositories
    against the pull request's apt-repo-action: nfsroot-watchdog
    (Set B, `all`), tmux (Set A, every architecture) and
    fpgas.online-fpga-tools (patch series, big). That's done with
    `workflow_dispatch` and a `ref` input, and it publishes nothing.

## 5. Adding a new convention

The same five steps every time:

1. **One pull request to apt-repo-action** changes packaging.md or
   conventions.md, adds the rule's ID and check with severity `warn`, and
   ideally the shared code that makes most repositories pass on their own.
2. **Merge.** The next nightly run adds a checkbox to the issue of every
   repository that fails it.
3. **Roll out.**
   - Shared-code fixes reach everything at once (`@main`).
   - Per-repository fixes go out as pull requests, scripted where the
     change is mechanical: `scripts/migrate/<rule-id>.py` opens a pull
     request in each failing repository.
   - A repository that can't follow the rule declares an exception, with
     its reason, in its own `.github/apt-packaging.toml`, reviewed like any
     other change.
4. **Enforce.** When every repository passes or has an exception, the rule's
   severity becomes `error`, and `--self` then fails a build that regresses.
5. **Removing or changing a rule** runs the same steps. The checker keeps
   an `introduced` date per rule, so the report can say how long each
   failure has been outstanding.

## 6. Rolling out today's conventions

In order of least effort per repository, so the report turns green fastest
and the shared code is proven on simple repositories before hard ones.

| phase | what | repositories |
|---|---|---|
| 0 | Merge packaging.md and the checker; add each repository's declaration; start the nightly report and issues | apt-repo-action, then all |
| 1 | `deb-version.py` (shared) and `build-deb.yml`; migrate the `Architecture: all` Set B repositories: names, triggers, concurrency, forky, `~deb<R>` | nfsroot-watchdog, rpi-hwid, sensors2mqtt, python-netgear-switch-library, ntrip-rtcm3-to-rtcm2p3 |
| 2 | Architecture-dependent Set B and patch series: the full architecture set and Raspbian; fpga-tools back to `@main` | go-claude-teleport, go-tmux-saver, fpgas.online-fpga-tools, rpi-qemu |
| 3 | Set A branch layout: rename the packaging branch to `packaging` and make it the default, create `upstream`, `sync-upstream.yml`, `packaging/README.md` | dnsmasq, netplan, usdr-lib, dtbocfg, ten64-microcontroller-utility, traverse-sensors, tmux |
| 4 | Set A repositories without upstream's history: import it, then as phase 3 | scanbd (a snapshot), paramiko-insecure (carries Debian's packaging history, not paramiko's) |
| 5 | Backport and aggregate: names, triggers, suites | paho-mqtt-bookworm, fpgas-online/apt |
| 6 | Enforce: every `PKG-*` rule to `error` | all |

From phase 1 on, each repository moves to the shared build in one commit
that also deletes its own version and build scripts: see
[Moving a repository to the shared build](packaging.md#moving-a-repository-to-the-shared-build).

**Version changes that need care**, each checked with
`dpkg --compare-versions` against what is published now:

- **tmux** (`3.8~git20260720…`) and **usdr-lib** (`0.9.10b~git20260909…`):
  count-based versions sort *below* the published date-based ones. Switch at
  the next upstream release, when `<release>-0+welland<M>` sorts above both
  schemes on its own; until then, record a temporary date exception.
- **scanbd**: `1.5.1-7+welland<M>` sorts below the published
  `1.5.1+welland4`, and upstream has had no release since 2017. It needs a
  one-time epoch (`1:`), recorded as an exception.
- **paramiko-insecure**: `+insecure1` to `+welland<M>` sorts higher. No
  special step.
- **rpi-qemu**: `2:0.1+112.g91e6fbe` to the patch series form
  `2:11.1.0+fpgasonline.0.1.post<N>` sorts higher. The epoch stays. Its old
  release job's `v0.1.<N>.g<sha>` tags aren't releases: the shared script
  only counts `vX.Y[.Z]`.
- **go-claude-teleport, go-tmux-saver**: the published `0.24` and `0.17` are
  bare tag versions, and `main` is at that tag. `0.24.post1~deb13` sorts
  higher, but a build of the tagged commit itself (`0.24~deb13`) would not:
  the first shared build is the migration commit, untagged.
- **libpio** (fpga-tools): stays date-based under its exception. Moving to
  `0.0+git<N>` needs an epoch.

**Cost.** The default matrix is 3 Debian suites × 5 architectures plus
2 Raspbian suites: 17 build jobs and 5 install tests per push for an
architecture-dependent repository. Measured on 2026-09-27 with
`tests/fixtures/hello` (a one-file C command), run 36296630494, 5 min
9 s end to end:

| job | runner | time |
|---|---|---|
| amd64, i386 (trixie, forky, sid) | ubuntu-24.04, native | 28–43 s (one i386 79 s) |
| arm64, armhf (trixie, forky, sid) | ubuntu-24.04-arm, native | 35–40 s |
| raspbian-trixie, raspbian-forky | ubuntu-24.04-arm, native | 77–88 s, bootstrapping the root (cold cache) |
| riscv64 (trixie, forky, sid) | ubuntu-24.04, QEMU | 231–258 s |
| install test, per suite | native | 13–22 s |

Only riscv64 is emulated now: GitHub's arm64 runners (Neoverse-N2) run
armhf and Raspbian natively, and bootstrapping a Raspbian root takes about
23 s there against about 140 s under QEMU on x86. riscv64 costs about
four minutes on a trivial package, and much more on a large one, so it
sets the wall-clock time. fpgas-online runs one job at a time. PR
previews for riscv64 may need to be limited on large repositories; that
would be recorded as an exception to "the same matrix as the default
branch", not done silently.

## 7. Later: move the signing key to the `github-pages` environment

A repository secret can be read by a workflow run from any branch of the
repository, including an unreviewed pull request branch in the same
repository. An environment secret is readable only by jobs running in that
environment, and packaging.md already limits `github-pages` to the default
branch. So:
1. Move `APT_GPG_PRIVATE_KEY` into the environment.
2. Change publish-apt.yml to read it through `secrets: inherit`.
3. Add a check (REPO-SECRET-ENV).

That is a new convention in its own right, and it would go through
section 5.
