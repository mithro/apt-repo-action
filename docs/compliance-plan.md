# Keeping every repository in compliance

A plan, not yet built, for how apt-repo-action brings every apt repository
in line with [packaging.md](packaging.md) and [conventions.md](conventions.md),
and keeps it there as conventions are added.

On 2026-09-25 no repository meets every rule. Of 21 repositories, 19 build
packages, and each of those fails between 6 and 17 of the 22 checks below.
Most failures are the same six rules, each failing in 16 to 20 repositories:
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
2. **Put the rule in shared code instead of in 21 copies.** The version
   script, the build matrix and the install test live in apt-repo-action,
   and every repository calls them at `@main`. Fixing it there fixes it
   everywhere.
3. **Exceptions are data.** They're listed in one file, with a reason, and
   the checker reads the same file. An unlisted difference is a failure, not
   a judgement call.
4. **New rules start as warnings.** A rule only fails builds once every
   repository passes it or has an exception, so adding a rule never breaks
   a publish.

## 1. The registry: `repositories.toml`

One entry per apt repository, at the root of apt-repo-action:

```toml
[repo."mithro/tmux"]
set = "A"                          # A, B, aggregate, retired
variant = ""                       # backport, patch-series
upstream = "https://github.com/tmux/tmux"
suites = "default"                 # or a list; anything but "default" needs an exception
architectures = "default"          # "default", "all", or a list

[[repo."fpgas-online/fpgas.online-fpga-tools".exception]]
rule = "PKG-ARCH"
value = "arm64 armhf"
reason = "hardware-specific: Raspberry Pi 5 (RP1 PIO JTAG)"
```

- It replaces the *Recorded exceptions* table in packaging.md; that table
  is generated from the registry.
- Discovery keeps it complete. The nightly job also lists every repository
  with GitHub Pages in both owners, and one that serves `<repo>.gpg` but
  isn't registered is reported. That is how rp1-jtag was found missing from
  the earlier audit.

## 2. The checker: `scripts/check-compliance.py`

Each rule has an ID, and each ID has a check:

| ID | rule | how it's checked |
|---|---|---|
| PKG-BRANCH | default branch `packaging` (A) or `main` (B) | GitHub API |
| PKG-HISTORY | Set A carries upstream's history | fork parent, or `upstream` shares commits with the upstream URL |
| PKG-UPSTREAM | Set A has an `upstream` branch | GitHub API |
| PKG-SYNC | Set A has `sync-upstream.yml` | file on the default branch |
| PKG-README | Set A has `packaging/README.md` | file |
| PKG-DEBIAN | `debian/` at the root of the default branch | file |
| PKG-WORKFLOW | `.github/workflows/deb.yml`, `name: Debian packages` | parse YAML |
| PKG-JOBS | jobs `test`, `build-deb`, `publish-apt`, `release` only | parse YAML |
| PKG-TRIGGERS | push to default + pull_request + workflow_dispatch; no workflow_run | parse YAML |
| PKG-PREVIEW | pull requests build, never publish | YAML + the publish job's `if:` |
| PKG-CONCURRENCY | `deb-${{ github.ref }}` group | parse YAML |
| PKG-PUBLISHER | `publish-apt.yml@main` | parse YAML |
| PKG-SHARED | shared build and version script; no local `deb-version.py` | YAML + files |
| PKG-SUITES | suites = default or registered exception | live site |
| PKG-ARCH | architectures = default or registered exception, nothing advertised without packages | live site |
| PKG-NODATES | no date in a version | live `Packages` |
| PKG-VERSION | version matches its set's form | live `Packages` |
| PKG-SUITE-SUFFIX | `~deb<R>` on every suite but sid | live `Packages` |
| PKG-INSTALL-TEST | an install test runs | YAML |
| PKG-MAINTAINER | `Maintainer: Tim 'mithro' Ansell <me@mith.ro>` | `debian/control` |
| PKG-DOCS | `## Install` with the setup lines | README |
| PKG-DBGSYM | no `-dbgsym` over 10 MB in apt | live `Packages` |
| REPO-* | everything in conventions.md: key files, layout, Origin/Label, index page | live site (`tmp/consist/verify.py` today) |

The prototype of the `PKG-*` checks, which produced the compliance report,
is `tmp/consist/compliance.py` in the working tree that wrote this plan. It
moves into `scripts/` with the registry replacing its hard-coded targets.

The checker has two modes:
- **`--all`** (nightly): every registered repository. It uses the GitHub
  API and the live sites, and writes `compliance.json` plus a Markdown
  report.
- **`--self`** (inside a build): the static checks for the calling
  repository only (branch, names, triggers, suites/architectures
  against the registry). This takes seconds and needs no network beyond
  the registry.

## 3. Move the rules into shared code

These do most of the work: once they exist, following the convention is
what a repository gets by default.

1. **`scripts/deb-version.py`**, one version script:
   - implements every form in packaging.md (Set A, Set B, backport, patch
     series), plus `~deb<R>` and `~pr<P>`;
   - has a test table, including every ordering in packaging.md;
   - replaces the 11 diverging copies of `packaging/deb-version.py`.
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
3. **`publish-apt.yml` enforces what can only be checked at publish time:**
   - refuses a version that isn't greater than what the suite already
     publishes (PKG-VERSION's "every push is newer");
   - refuses to advertise an architecture with no packages;
   - runs `check-compliance --self` and prints its warnings, turning into
     errors per rule as described below.
4. **Scaffolding for new repositories**:
   - `scripts/new-repo.py --set B` writes `deb.yml`, `debian/` and the README
     install section;
   - `--set A --upstream <url>` forks or imports with history, creates
     `upstream` and `packaging`, and adds `sync-upstream.yml`.

   A new repository is compliant from its first commit.

## 4. Continuous checking

- **Nightly `compliance.yml` in apt-repo-action** runs `check-compliance
  --all` plus discovery, then:
  - publishes the report on apt-repo-action's own Pages site: the tables
    and todo lists of the compliance report, from live data;
  - keeps **one issue per non-compliant repository**, in that repository,
    titled `apt conventions: N rules failing`. The body is the todo list as
    checkboxes, and it closes itself when the repository passes.
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
   - A repository that can't follow the rule gets a registry exception, with
     its reason, reviewed like any other change.
4. **Enforce.** When every repository passes or has an exception, the rule's
   severity becomes `error`, and `--self` then fails a build that regresses.
5. **Removing or changing a rule** runs the same steps. The registry keeps
   an `introduced` date per rule, so the report can say how long each
   failure has been outstanding.

## 6. Rolling out today's conventions

In order of least effort per repository, so the report turns green fastest
and the shared code is proven on simple repositories before hard ones.

| phase | what | repositories |
|---|---|---|
| 0 | Merge packaging.md; add the registry and the checker, report-only; start the nightly report and issues | apt-repo-action |
| 1 | `deb-version.py` (shared) and `build-deb.yml`; migrate the `Architecture: all` Set B repositories: names, triggers, concurrency, forky, `~deb<R>` | nfsroot-watchdog, rpi-hwid, sensors2mqtt, python-netgear-switch-library, ntrip-rtcm3-to-rtcm2p3 |
| 2 | Architecture-dependent Set B and patch series: the full architecture set and Raspbian; fpga-tools back to `@main` | go-claude-teleport, go-tmux-saver, fpgas.online-fpga-tools, rpi-qemu |
| 3 | Set A branch layout: rename the packaging branch to `packaging` and make it the default, create `upstream`, `sync-upstream.yml`, `packaging/README.md` | dnsmasq, netplan, usdr-lib, dtbocfg, ten64-microcontroller-utility, traverse-sensors |
| 4 | Set A repositories that are flat snapshots: re-import upstream history, then as phase 3 | tmux, scanbd, paramiko-insecure |
| 5 | Backport and aggregate: names, triggers, suites | paho-mqtt-bookworm, fpgas-online/apt |
| 6 | Enforce: every `PKG-*` rule to `error` | all |

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
- **rpi-qemu**: `2:0.1+97.g<sha>` to `2:0.1.post<N>` sorts higher. The epoch
  stays.
- **libpio** (fpga-tools): stays date-based under its exception. Moving to
  `0.0+git<N>` needs an epoch.

**Cost.** The default matrix is up to 5 suites × 5 architectures, 25 build
jobs per push for an architecture-dependent repository, and riscv64, armhf
and Raspbian run under emulation. fpgas-online runs one job at a time.
Phase 2 should measure a full matrix on one repository first, and PR
previews for the slow emulated architectures may need to be limited. That
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
