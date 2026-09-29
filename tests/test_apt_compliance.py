"""Tests for the workflow- and file-reading rules of scripts/apt-compliance.py
(docs/compliance-plan.md, section 2). No network: each rule's helper is fed
the parsed workflow or file text it would get from GitHub.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
Needs PyYAML (python3-yaml), as the script does.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/apt-compliance.py"
_loader = importlib.machinery.SourceFileLoader("apt_compliance", str(SCRIPT))
apc = importlib.util.module_from_spec(importlib.util.spec_from_loader("apt_compliance", _loader))
_loader.exec_module(apc)

ACTION = "someone/apt-repo-action"


def jobs(text: str) -> dict:
    return yaml.safe_load(text)["jobs"]


class InstallTest(unittest.TestCase):
    def test_step_in_the_build_job(self):
        ok, detail = apc.install_test(jobs("""
jobs:
  build-deb:
    steps:
      - uses: someone/apt-repo-action/build-deb@main
      - name: Install test
        run: docker run --rm debian:trixie true
"""), ACTION)
        self.assertTrue(ok, detail)
        self.assertIn("build-deb", detail)

    def test_reusable_build_workflow_counts(self):
        ok, _ = apc.install_test(jobs("""
jobs:
  build-deb:
    uses: someone/apt-repo-action/.github/workflows/build-deb.yml@main
"""), ACTION)
        self.assertTrue(ok)

    def test_nfpm_build_job(self):
        ok, detail = apc.install_test(jobs("""
jobs:
  build:
    steps:
      - run: nfpm package --packager deb
      - name: Install test
        run: sudo apt-get install -y ./dist/*.deb
"""), ACTION)
        self.assertTrue(ok, detail)

    def test_step_in_another_job_does_not_count(self):
        ok, detail = apc.install_test(jobs("""
jobs:
  build-deb:
    steps:
      - uses: someone/apt-repo-action/build-deb@main
  test:
    steps:
      - name: Install test
        run: true
"""), ACTION)
        self.assertFalse(ok)
        self.assertEqual(detail, "no `Install test` step in build-deb")

    def test_a_step_that_runs_nothing(self):
        ok, _ = apc.install_test(jobs("""
jobs:
  build-deb:
    steps:
      - uses: someone/apt-repo-action/build-deb@main
      - name: Install test
        run: |
          # TODO
"""), ACTION)
        self.assertFalse(ok)

    def test_name_must_match(self):
        # nfsroot-watchdog's test job extracts a .deb (dpkg-deb -x: not a
        # build) in a step named "Install test dependencies".
        text = """
jobs:
  test:
    steps:
      - name: Install test dependencies
        run: apt-get download busybox-static && dpkg-deb -x busybox-static_*.deb /tmp/bbs
  build-deb:
    steps:
      - uses: someone/apt-repo-action/build-deb@main
"""
        self.assertEqual(apc.install_test(jobs(text), ACTION), (False, "no `Install test` step in build-deb"))
        text += "      - name: Install test (${{ matrix.suite }})\n        run: true\n"
        self.assertEqual(apc.install_test(jobs(text), ACTION),
                         (True, "`Install test (${{ matrix.suite }})` in build-deb"))

    def test_local_reusable_workflow(self):
        # fpgas.online-fpga-tools: debs.yml's build job calls build-debs.yml.
        caller = jobs("""
jobs:
  build:
    uses: ./.github/workflows/build-debs.yml
  publish:
    uses: someone/apt-repo-action/.github/workflows/publish-apt.yml@main
""")
        inner = {"jobs": jobs("""
jobs:
  debs:
    steps:
      - run: dpkg-buildpackage -b -us -uc
      - name: Install test
        run: apt-get install -y ./*.deb
""")}
        self.assertEqual(apc.install_test(caller, ACTION, {"build-debs.yml": inner}),
                         (True, "`Install test` in build/debs"))
        del inner["jobs"]["debs"]["steps"][1]
        self.assertEqual(apc.install_test(caller, ACTION, {"build-debs.yml": inner}),
                         (False, "no `Install test` step in build/debs"))

    def test_build_in_a_script(self):
        # netplan: the build runs in packaging/ci-build-raspbian.sh.
        self.assertEqual(apc.install_test(jobs("""
jobs:
  test:
    steps: [{run: make check}]
  build-raspbian:
    steps: [{run: bash packaging/ci-build-raspbian.sh}]
  publish:
    uses: someone/apt-repo-action/.github/workflows/publish-apt.yml@main
"""), ACTION), (False, "no `Install test` step in build-raspbian"))

    def test_no_build_job(self):
        self.assertEqual(apc.install_test(jobs("jobs:\n  test:\n    steps:\n      - run: make\n"), ACTION),
                         (False, "no job builds the packages"))


PYPI = """
name: Publish to PyPI
on:
  workflow_run:
    workflows: ["Debian packages"]
    types: [completed]
    branches: [main]
  workflow_dispatch:
jobs:
  build:
    if: >-
      github.event_name == 'workflow_dispatch' ||
      (github.event.workflow_run.conclusion == 'success' &&
       github.event.workflow_run.event != 'pull_request')
    runs-on: ubuntu-latest
    steps: [{run: uv build}]
  publish:
    needs: build
    runs-on: ubuntu-latest
    steps: [{uses: pypa/gh-action-pypi-publish@release/v1}]
"""


class WorkflowRun(unittest.TestCase):
    def check(self, text: str) -> list:
        return apc.unguarded_workflow_runs({"publish-pypi.yml": yaml.safe_load(text)})

    def test_guarded_and_its_dependents(self):
        # rpi-hwid's publish-pypi.yml, as merged in rpi-hwid#42.
        self.assertEqual(self.check(PYPI), [])

    def test_success_only_is_not_enough(self):
        text = PYPI.replace(" &&\n       github.event.workflow_run.event != 'pull_request'", "")
        self.assertEqual(self.check(text),
                         ["publish-pypi.yml: workflow_run jobs build publish not guarded against pull requests"])

    def test_a_dependent_that_runs_anyway(self):
        text = PYPI.replace("    needs: build\n", "    needs: build\n    if: always()\n")
        self.assertEqual(self.check(text),
                         ["publish-pypi.yml: workflow_run job publish not guarded against pull requests"])

    def test_double_quotes_and_other_workflows(self):
        text = PYPI.replace("'pull_request')", '"pull_request")')
        self.assertEqual(self.check(text), [])
        self.assertEqual(apc.unguarded_workflow_runs({"ci.yml": {True: ["push"], "jobs": {"a": {}}}}), [])

    def test_triggers_forms(self):
        self.assertEqual(apc.triggers({True: "push"}), {"push": None})
        self.assertEqual(apc.triggers({"on": ["push", "pull_request"]}), {"push": None, "pull_request": None})
        self.assertIn("workflow_run", apc.triggers(yaml.safe_load(PYPI)))


# A caller of the reusable workflow, as README.md shows it.
REUSABLE = """
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: make check
  build-deb:
    needs: test
    uses: someone/apt-repo-action/.github/workflows/build-deb.yml@main
  publish-apt:
    if: github.event_name != 'pull_request' && github.ref_name == github.event.repository.default_branch
    needs: build-deb
    uses: someone/apt-repo-action/.github/workflows/publish-apt.yml@main
    with:
      suites: ${{ needs.build-deb.outputs.suites }}
      architectures: ${{ needs.build-deb.outputs.architectures }}
"""

# An nfpm build versioned by the deb-version action, as go-tmux-saver's.
NFPM = """
jobs:
  build-deb:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - name: Version (trixie)
        id: v-trixie
        uses: someone/apt-repo-action/deb-version@REF
        with:
          suite: trixie
      - name: Build
        run: |
          go build -o dist/x ./cmd/x
          VERSION="$V" nfpm package -p deb -f nfpm.yaml -t built-debs/trixie/
      - name: Install test
        run: docker run --rm debian:trixie true
  release:
    runs-on: ubuntu-latest
    steps:
      - name: Version
        uses: someone/apt-repo-action/deb-version@RELEASE_REF
        with:
          suite: sid
"""


def nfpm(ref="main", release_ref="main"):
    return jobs(NFPM.replace("RELEASE_REF", release_ref).replace("@REF", "@" + ref))


class Shared(unittest.TestCase):
    def shared(self, j, local_ver=False, nfpm_build=False, variant=""):
        return apc.shared_build(j, ACTION, local_ver, nfpm_build, variant)

    def test_reusable_workflow(self):
        self.assertEqual(self.shared(jobs(REUSABLE)), (True, "the reusable build-deb.yml"))

    def test_reusable_workflow_off_main(self):
        ok, detail = self.shared(jobs(REUSABLE.replace("build-deb.yml@main", "build-deb.yml@v1")))
        self.assertFalse(ok)
        self.assertIn("build-deb.yml@v1 (want @main)", detail)

    def test_reusable_workflow_is_an_install_test(self):
        self.assertTrue(apc.install_test(jobs(REUSABLE), ACTION)[0])

    def test_build_deb_action(self):
        j = jobs("""
jobs:
  build-deb:
    runs-on: ubuntu-24.04
    steps:
      - uses: someone/apt-repo-action/build-deb@main
        with: {suite: trixie, arch: all}
""")
        self.assertEqual(self.shared(j), (True, "the build-deb action"))

    def test_nfpm_with_deb_version(self):
        self.assertEqual(self.shared(nfpm(), nfpm_build=True), (True, "nfpm with the deb-version action"))

    def test_nfpm_deb_version_off_main(self):
        ok, detail = self.shared(nfpm("deb-version/patch-series"), nfpm_build=True)
        self.assertFalse(ok)
        self.assertEqual(detail, "deb-version@deb-version/patch-series (want @main)")

    def test_release_job_counts(self):
        # The release job's version must be the same script as the build's.
        ok, detail = self.shared(nfpm(release_ref="v2"), nfpm_build=True)
        self.assertFalse(ok)
        self.assertEqual(detail, "deb-version@v2 (want @main)")

    def test_deb_version_alone_is_not_a_shared_build(self):
        # dpkg-buildpackage with only the version from deb-version: that is
        # what build-deb is for.
        ok, detail = self.shared(nfpm())
        self.assertFalse(ok)
        self.assertIn("neither nfpm nor a patch series", detail)

    def test_patch_series_own_job(self):
        self.assertEqual(self.shared(nfpm(), variant="patch-series"),
                         (True, "the deb-version action in its own job"))

    def test_local_deb_version(self):
        ok, detail = self.shared(jobs(REUSABLE), local_ver=True)
        self.assertFalse(ok)
        self.assertEqual(detail, "the reusable build-deb.yml; local deb-version.py")

    def test_own_build(self):
        j = jobs("""
jobs:
  build-deb:
    runs-on: ubuntu-24.04
    steps:
      - run: dpkg-buildpackage -b
""")
        self.assertEqual(self.shared(j), (False, "own build steps"))

    def test_another_repositorys_actions_dont_count(self):
        self.assertEqual(self.shared(jobs(REUSABLE.replace("someone/", "else/"))), (False, "own build steps"))

    def test_nfpm_install_test(self):
        self.assertTrue(apc.install_test(nfpm(), ACTION)[0])


CONTROL_ANY = "Source: x\n\nPackage: x\nArchitecture: any\n\nPackage: x-doc\nArchitecture: all\n"
CONTROL_ALL = "Source: x\n\nPackage: x\nArchitecture: all\n"

# Declarations as docs/packaging.md allows them, with what build-matrix.py
# (the reusable build-deb.yml's planner) does with each: (declaration,
# debian/control, refused).
MATRIX_CASES = [
    ({}, CONTROL_ANY, False),
    ({}, CONTROL_ALL, True),
    ({"architectures": "all"}, CONTROL_ALL, False),
    ({"architectures": "all"}, CONTROL_ANY, True),
    ({"architectures": "default"}, CONTROL_ANY, False),
    ({"architectures": ["arm64", "armhf"]}, CONTROL_ANY, False),
    ({"architectures": "arm64 armhf"}, CONTROL_ANY, False),
    ({"architectures": ["arm64"]}, CONTROL_ANY, False),
    ({"architectures": ["arm64", "sparc64"]}, CONTROL_ANY, True),
    ({"architectures": ["arm64", "arm64"]}, CONTROL_ANY, True),
    ({"architectures": "all", "suites": ["bookworm", "trixie", "forky", "sid"]}, CONTROL_ALL, False),
    ({"suites": ["bookworm", "trixie", "forky", "sid", "raspbian-bookworm", "raspbian-trixie"]}, CONTROL_ANY, False),
    ({"suites": "trixie sid"}, CONTROL_ANY, False),
    ({"architectures": "all", "suites": ["trixie", "raspbian-trixie"]}, CONTROL_ALL, True),
    ({"architectures": ["arm64"], "suites": ["trixie", "raspbian-trixie"]}, CONTROL_ANY, True),
    ({"suites": ["trixie", "raspbian-sid"]}, CONTROL_ANY, True),
    ({"suites": ["bookworm"], "architectures": ["amd64", "riscv64"]}, CONTROL_ANY, False),
]


class Matrix(unittest.TestCase):
    def test_defaults(self):
        m = apc.declared_matrix({}, set(), CONTROL_ANY)
        self.assertEqual(m["archs"], apc.DEFAULT_ARCH)
        self.assertEqual(m["suites"], ["trixie", "forky", "sid", "raspbian-trixie", "raspbian-forky"])
        self.assertTrue(m["arch_default"] and m["suites_default"])
        self.assertEqual(m["matrix_problems"], [])

    def test_all(self):
        m = apc.declared_matrix({"architectures": "all"}, set(), CONTROL_ALL)
        self.assertEqual((m["archs"], m["suites"]), ([], ["trixie", "forky", "sid"]))

    def test_default_is_any(self):
        self.assertEqual(apc.declared_matrix({"architectures": "default"}, set(), None)["archs"], apc.DEFAULT_ARCH)

    def test_strings_are_words(self):
        m = apc.declared_matrix({"architectures": "arm64 armhf", "suites": "trixie raspbian-trixie"}, set(), None)
        self.assertEqual((m["archs"], m["suites"]), (["arm64", "armhf"], ["trixie", "raspbian-trixie"]))
        self.assertFalse(m["arch_default"] or m["suites_default"])

    def test_undeclared_infers_all_from_control(self):
        self.assertEqual(apc.declared_matrix(None, set(), CONTROL_ALL)["architectures"], "all")

    def test_undeclared_published_must_agree(self):
        # paramiko-insecure: an all-`all` debian/control, but it also
        # publishes an architecture-dependent package built from elsewhere.
        self.assertEqual(apc.declared_matrix(None, {"all", "amd64"}, CONTROL_ALL)["architectures"], "any")

    def test_declared_without_architectures_is_the_default(self):
        m = apc.declared_matrix({"kind": "B"}, {"all"}, None)
        self.assertEqual((m["architectures"], m["archs"]), ("any", apc.DEFAULT_ARCH))

    def test_undeclared_infers_all_from_published(self):
        self.assertEqual(apc.declared_matrix(None, {"all"}, None)["architectures"], "all")
        self.assertEqual(apc.declared_matrix(None, {"all", "amd64"}, None)["architectures"], "any")

    def test_problems(self):
        for decl, control, refused in MATRIX_CASES:
            with self.subTest(decl=decl, control=control.splitlines()[-1]):
                self.assertEqual(bool(apc.declared_matrix(decl, set(), control)["matrix_problems"]), refused)

    def test_same_as_build_matrix(self):
        """The same suites and architectures as the reusable workflow builds,
        and refused exactly when it refuses."""
        script = SCRIPT.with_name("build-matrix.py")
        if not script.exists():
            self.skipTest("no scripts/build-matrix.py (the reusable build-deb.yml)")
        loader = importlib.machinery.SourceFileLoader("build_matrix", str(script))
        bm = importlib.util.module_from_spec(importlib.util.spec_from_loader("build_matrix", loader))
        loader.exec_module(bm)
        for decl, control, refused in MATRIX_CASES:
            with self.subTest(decl=decl, control=control.splitlines()[-1]):
                m = apc.declared_matrix(decl, set(), control)
                try:
                    plan = bm.plan(decl, bm.control_architectures(control), "declared", "declared")
                except bm.Error:
                    self.assertTrue(refused)
                    self.assertTrue(m["matrix_problems"])
                    continue
                self.assertFalse(refused)
                self.assertEqual(m["matrix_problems"], [])
                self.assertEqual(m["suites"], plan["suites"])
                t = {"archs": m["archs"]}
                for suite in plan["suites"]:
                    self.assertEqual(apc.arch_for(suite, t),
                                     {j["arch"] for j in plan["build"] if j["suite"] == suite}, suite)


class Changelog(unittest.TestCase):
    ROOT = ["debian/control", "debian/rules", ".gitignore"]

    def test_set_b_ignored(self):
        self.assertEqual(apc.changelog(self.ROOT, "tmp/\ndebian/changelog\n", "B", False),
                         (True, "not committed; ignored"))
        self.assertTrue(apc.changelog(self.ROOT, "/debian/changelog\n", "B", False)[0])

    def test_set_b_committed(self):
        self.assertEqual(apc.changelog(self.ROOT + ["debian/changelog"], "debian/changelog\n", "B", False),
                         (False, "commits debian/changelog"))

    def test_set_b_not_ignored(self):
        self.assertEqual(apc.changelog(self.ROOT, "tmp/\n# debian/changelog\n", "B", False),
                         (False, "not committed, but not in .gitignore"))
        self.assertFalse(apc.changelog(self.ROOT, None, "B", False)[0])

    def test_patch_series_templates(self):
        files = ["packaging/debian/openocd/control", "packaging/debian/openocd/rules"]
        self.assertEqual(apc.changelog(files, None, "B", False), (True, "no committed changelog"))
        self.assertEqual(apc.changelog(files + ["packaging/debian/openocd/changelog"], None, "B", False),
                         (False, "commits packaging/debian/openocd/changelog"))

    def test_set_a_and_nfpm(self):
        self.assertIsNone(apc.changelog(self.ROOT + ["debian/changelog"], None, "A", False)[0])
        self.assertEqual(apc.changelog(["nfpm.yaml"], None, "B", True), (None, "nfpm build"))

    def test_mirror_is_as_set_b(self):
        self.assertEqual(apc.changelog(self.ROOT, "/debian/changelog\n", "mirror", False),
                         (True, "not committed; ignored"))
        self.assertFalse(apc.changelog(self.ROOT + ["debian/changelog"], None, "mirror", False)[0])


# fpgas-online/migen's, on packaging (2026-09-27), trimmed to what matters.
MIRROR_SYNC = """name: Sync upstream
on:
  schedule:
    - cron: "0 6 * * *" # daily 06:00 UTC
  workflow_dispatch:
permissions:
  contents: write # push the mirrored branches and tags
  actions: write # start deb.yml
jobs:
  sync:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - id: mirror
        run: python3 packaging/sync-mirror.py
      - if: steps.mirror.outputs.build == 'true'
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh workflow run deb.yml --ref packaging --repo "$GITHUB_REPOSITORY"
"""
MIRROR_SHARED = """name: Sync upstream
on:
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:
permissions:
  contents: write
  actions: write
jobs:
  sync:
    uses: someone/apt-repo-action/.github/workflows/sync-mirror.yml@main
"""
MIRROR_DECL = {"kind": "mirror", "upstream": "https://git.m-labs.hk/M-Labs/migen.git",
               "mirror": {"build": "master", "ours": ["github-master", "legacy", "experimental"]}}


class Mirror(unittest.TestCase):
    NOW = 1790000000.0  # 2026-09-21T12:53:20Z

    def facts(self, **mirror):
        m = {"build_present": True, "shared_history": False, "ours": "a" * 40, "theirs": "a" * 40,
             "theirs_error": None, "sync_runs": []}
        m.update(mirror)
        return {"default": "packaging", "mirror": m, "workflows": {"sync-upstream.yml": MIRROR_SHARED}}

    def rules(self, f, decl=MIRROR_DECL):
        t = {"mirror": apc.mirror_declaration(decl)[0]}
        return {r: (ok, d) for r, ok, d in apc.mirror_rules(f, t, ACTION, now=self.NOW)}

    def test_declaration(self):
        self.assertEqual(apc.mirror_declaration(MIRROR_DECL),
                         ({"build": "master", "ours": ["github-master", "legacy", "experimental"]}, []))
        self.assertEqual(apc.mirror_declaration({**MIRROR_DECL, "upstream": "git@example.org:x/y.git"})[1], [])

    def test_declaration_problems(self):
        def probs(**kw):
            return apc.mirror_declaration({**MIRROR_DECL, **kw})[1]
        self.assertIn("a mirror's `upstream` must be the git URL it copies", probs(upstream=None))
        self.assertIn("a mirror needs a [mirror] table with `build`", probs(mirror="master"))
        self.assertIn("[mirror] build must name the branch the package is built from", probs(mirror={}))
        self.assertIn("[mirror] build master is one of our own branches",
                      probs(mirror={"build": "master", "ours": ["master"]}))
        self.assertIn("[mirror] build packaging is one of our own branches", probs(mirror={"build": "packaging"}))
        self.assertIn("[mirror] ours must be a list of branch names", probs(mirror={"build": "master", "ours": "x"}))
        self.assertIn("[mirror] has unknown keys branch", probs(mirror={"build": "master", "branch": "x"}))

    def test_sync(self):
        self.assertEqual(apc.mirror_sync(MIRROR_SHARED, ACTION),
                         (True, "sync-upstream.yml: scheduled, the shared sync-mirror.yml"))
        self.assertEqual(apc.mirror_sync(None, ACTION), (False, "no sync-upstream.yml"))
        self.assertEqual(apc.mirror_sync(MIRROR_SHARED.replace("@main", "@v1"), ACTION),
                         (False, "sync-mirror.yml@v1 (want @main)"))
        self.assertEqual(apc.mirror_sync(MIRROR_SHARED.replace('  schedule:\n    - cron: "0 6 * * *"\n', ""), ACTION),
                         (False, "not scheduled"))
        self.assertFalse(apc.mirror_sync(MIRROR_SHARED.replace("name: Sync upstream", "name: Mirror"), ACTION)[0])

    def test_own_sync(self):
        # migen's own steps start deb.yml, but aren't the shared sync.
        self.assertEqual(apc.mirror_sync(MIRROR_SYNC, ACTION),
                         (False, "its own sync steps, not someone/apt-repo-action/.github/workflows/sync-mirror.yml@main"))
        # Only in a comment: it doesn't start anything.
        self.assertEqual(apc.mirror_sync(MIRROR_SYNC.replace("run: gh workflow run", "run: true # gh workflow run"), ACTION),
                         (False, "doesn't start deb.yml"))

    def test_in_step(self):
        r = self.rules(self.facts())
        self.assertEqual(r["PKG-HISTORY"], (True, "packaging shares no history with master"))
        self.assertEqual(r["PKG-UPSTREAM"], (True, "master is upstream's (aaaaaaaaaaaa)"))
        self.assertEqual(r["PKG-SYNC"], (True, "sync-upstream.yml: scheduled, the shared sync-mirror.yml"))

    def test_shared_history(self):
        self.assertEqual(self.rules(self.facts(shared_history=True))["PKG-HISTORY"],
                         (False, "packaging shares history with master"))

    def test_upstream_moved_since_a_recent_sync(self):
        recent = [{"conclusion": None, "created_at": "2026-09-21T12:00:00Z"},  # running: not counted
                  {"conclusion": "success", "created_at": "2026-09-21T06:00:00Z"}]
        ok, detail = self.rules(self.facts(theirs="b" * 40, sync_runs=recent))["PKG-UPSTREAM"]
        self.assertTrue(ok, detail)
        self.assertIn("upstream moved since the last sync", detail)

    def test_upstream_moved_and_the_sync_is_failing_or_stale(self):
        failing = [{"conclusion": "failure", "created_at": "2026-09-21T06:00:00Z"}]
        stale = [{"conclusion": "success", "created_at": "2026-09-18T06:00:00Z"}]
        for runs, want in ((failing, "last sync failure"), (stale, "last sync success 2026-09-18"), ([], "no sync has run")):
            ok, detail = self.rules(self.facts(theirs="b" * 40, sync_runs=runs))["PKG-UPSTREAM"]
            self.assertFalse(ok)
            self.assertIn(want, detail)

    def test_upstream_unreachable(self):
        self.assertEqual(self.rules(self.facts(theirs=None, theirs_error="upstream has no master branch"))["PKG-UPSTREAM"],
                         (False, "upstream: upstream has no master branch"))

    def test_no_build_branch(self):
        r = self.rules(self.facts(build_present=False))
        self.assertEqual(r["PKG-HISTORY"], (False, "no master branch"))
        self.assertEqual(r["PKG-UPSTREAM"], (False, "no master branch"))
        r = self.rules(self.facts(), decl={**MIRROR_DECL, "mirror": {}})
        self.assertEqual(r["PKG-UPSTREAM"], (False, "no [mirror] build declared"))

    def target_facts(self, declaration):
        return {"declaration": declaration, "site": None, "workflows": {"deb.yml": "run: dpkg-buildpackage"},
                "fork": True, "branches": ["packaging", "master"], "upstream_authors": [], "files": [],
                "debian/control": "Source: x\n\nPackage: x\nArchitecture: all\n"}

    def test_declared_not_inferred(self):
        # A GitHub fork with a packaging default branch is Set A, unless it says otherwise.
        self.assertEqual(apc.target(self.target_facts(None), "fpgasonline")["kind"], "A")
        decl = ('kind = "mirror"\nupstream = "https://git.m-labs.hk/M-Labs/migen.git"\narchitectures = "all"\n'
                '[mirror]\nbuild = "master"\n')
        t = apc.target(self.target_facts(decl), "fpgasonline")
        self.assertEqual((t["kind"], t["mirror"], t["mirror_problems"]), ("mirror", {"build": "master", "ours": []}, []))


SITE = "https://pkgs.example.com/widget"
FPR = "9DD7CAB5516449861B2243E613136D7A38B0462E"
README = f"""# widget

## Install

```sh
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL {SITE}/widget.gpg | sudo tee /etc/apt/keyrings/widget.gpg > /dev/null
## a shell comment, not a heading
echo "deb [signed-by=/etc/apt/keyrings/widget.gpg] {SITE}/trixie/ ./" \\
  | sudo tee /etc/apt/sources.list.d/widget.list
sudo apt update
```

The signing key is `9DD7 CAB5 5164 4986 1B22  43E6 1313 6D7A 38B0 462E`.

### On bookworm

```sh
curl -fsSL https://pkgs.example.com/dep-backport/dep-backport.gpg | sudo tee /etc/apt/keyrings/dep-backport.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/dep-backport.gpg] https://pkgs.example.com/dep-backport/bookworm/ ./" \\
  | sudo tee /etc/apt/sources.list.d/dep-backport.list
```

## Usage

deb [signed-by=/etc/apt/keyrings/other.gpg] https://example.org/other/trixie/ ./
"""
DEP = {"repo": "someone/dep-backport", "name": "dep-backport", "suites": ["bookworm"]}


def old_format_key(body: bytes) -> bytes:
    """A public key packet, old format, two-octet length."""
    return bytes([0x99]) + len(body).to_bytes(2, "big") + body


class Docs(unittest.TestCase):
    def setUp(self):
        apc.pages_site.cache_clear()
        self.addCleanup(apc.pages_site.cache_clear)
        self.sites = {"someone/dep-backport": "https://pkgs.example.com/dep-backport"}
        patch = unittest.mock.patch.object(apc, "api", lambda path: {"html_url": self.sites.get(
            path.removeprefix("repos/").removesuffix("/pages"), "")})
        patch.start()
        self.addCleanup(patch.stop)

    def docs(self, doc, fprs=(FPR,), depends=()):
        with unittest.mock.patch.object(apc, "key_fingerprints", lambda key: list(fprs)):
            return apc.docs(doc, "widget", SITE, b"key", list(depends))

    def test_section_bounds(self):
        section = apc.install_section(README)
        self.assertIn("On bookworm", section)
        self.assertNotIn("other.gpg", section)
        self.assertIsNone(apc.install_section("## Installation\n\n### Install\n"))
        self.assertEqual(apc.install_section("# x\n## Install ##\nhere\n"), "here")

    def test_complete(self):
        self.assertEqual(self.docs(README, depends=[DEP]),
                         (True, "Install section with the setup and the key fingerprint"))

    def test_heading_level_and_text(self):
        for heading in ("### Install", "## Installation", "# Install"):
            self.assertEqual(self.docs(README.replace("## Install\n", heading + "\n")),
                             (False, "no `## Install` section"))

    def test_setup_outside_the_section_does_not_count(self):
        doc = README.replace("## Install\n", "## Build\n") + "\n## Install\n\nSee above.\n"
        ok, detail = self.docs(doc)
        self.assertFalse(ok)
        self.assertIn(f"no key download from {SITE}/widget.gpg", detail)
        self.assertIn(f"no sources line for {SITE}/<suite>/", detail)

    def test_fingerprint(self):
        self.assertEqual(self.docs(README, fprs=["0" * 40]), (False, "no key fingerprint"))
        self.assertTrue(self.docs(README, fprs=[])[0])  # no key to read: nothing to compare

    def test_dependency_repository(self):
        ok, detail = self.docs(README.replace("dep-backport", "elsewhere"), depends=[DEP])
        self.assertFalse(ok)
        self.assertIn("dependency dep-backport: no key download", detail)
        self.sites.clear()
        apc.pages_site.cache_clear()
        self.assertEqual(self.docs(README, depends=[DEP]), (False, "dependency dep-backport: no Pages site"))

    def test_third_party_dependency(self):
        third = {"name": "example", "url": "https://example.org/debian", "suite": "{codename}",
                 "key": "https://example.org/key.asc"}
        self.assertFalse(self.docs(README, depends=[third])[0])
        doc = README.replace("## Usage", "curl -fsSL https://example.org/key.asc | sudo tee "
                             "/etc/apt/keyrings/example.asc\nhttps://example.org/debian trixie main\n\n## Usage")
        self.assertTrue(self.docs(doc, depends=[third])[0])

    def test_line_continuation(self):
        doc = README.replace(f"widget.gpg] {SITE}", f"widget.gpg] \\\n    {SITE}")
        self.assertIn("\\\n    https://", doc)
        self.assertTrue(self.docs(doc)[0])

    def test_published_suites_named(self):
        doc = README.replace(
            "The signing key", "Suites: bookworm, trixie and raspbian-trixie; put yours in place of trixie.\n\nThe signing key")
        with unittest.mock.patch.object(apc, "key_fingerprints", lambda key: [FPR]):
            self.assertTrue(apc.docs(doc, "widget", SITE, b"", [], ["bookworm", "trixie", "raspbian-trixie"])[0])
            self.assertEqual(apc.docs(doc, "widget", SITE, b"", [], ["trixie", "forky", "sid"]),
                             (False, "doesn't name the published suites forky sid"))
            # raspbian-forky doesn't name forky
            doc2 = doc.replace("raspbian-trixie", "raspbian-forky")
            self.assertEqual(apc.docs(doc2, "widget", SITE, b"", [], ["forky"]),
                             (False, "doesn't name the published suite forky"))

    def test_forbidden(self):
        doc = README.replace("sudo apt update", "sudo apt update\nlsb_release -cs")
        self.assertEqual(self.docs(doc), (False, "mentions lsb_release"))

    def test_fingerprints_of_a_packet(self):
        import hashlib
        body = bytes([4, 0, 0, 0, 0, 22]) + b"x" * 40
        want = hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest().upper()
        self.assertEqual(apc.key_fingerprints(old_format_key(body)), [want])
        # a user ID packet (tag 13, new format) after it is skipped
        self.assertEqual(apc.key_fingerprints(old_format_key(body) + bytes([0xCD, 3]) + b"abc"), [want])
        self.assertEqual(apc.key_fingerprints(b"-----BEGIN PGP"), [])

    @unittest.skipUnless(shutil.which("gpg"), "needs gpg")
    def test_fingerprints_match_gpg(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        home.chmod(0o700)
        env = {**os.environ, "GNUPGHOME": str(home)}

        def gpg(*a):
            return subprocess.run(["gpg", "--batch", "--passphrase", "", *a], env=env,
                                  check=True, capture_output=True).stdout
        gpg("--quick-gen-key", "apt-compliance test <test@invalid>", "ed25519", "sign", "never")
        self.addCleanup(subprocess.run, ["gpgconf", "--kill", "gpg-agent"], env=env)
        want = [l.split(":")[9] for l in gpg("--with-colons", "--fingerprint").decode().splitlines()
                if l.startswith("fpr:")][:1]
        self.assertEqual(apc.key_fingerprints(gpg("--export")), want)


if __name__ == "__main__":
    unittest.main()
