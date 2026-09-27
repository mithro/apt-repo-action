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


if __name__ == "__main__":
    unittest.main()
