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
