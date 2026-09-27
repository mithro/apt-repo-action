"""Tests for the workflow- and file-reading rules of scripts/apt-compliance.py
(docs/compliance-plan.md, section 2). No network: each rule's helper is fed
the parsed workflow or file text it would get from GitHub.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
Needs PyYAML (python3-yaml), as the script does.
"""
import importlib.machinery
import importlib.util
import unittest
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


if __name__ == "__main__":
    unittest.main()
