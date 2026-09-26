"""Tests for scripts/deb-version.py against docs/packaging.md ("Versions").

Run: python3 -m unittest discover -s tests -p 'test_*.py'
The ordering tests need dpkg (dpkg --compare-versions); the tree tests need git.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/deb-version.py"
_loader = importlib.machinery.SourceFileLoader("deb_version", str(SCRIPT))
dv = importlib.util.module_from_spec(importlib.util.spec_from_loader("deb_version", _loader))
_loader.exec_module(dv)

CONTROL = """\
Source: selftest-src
Maintainer: Self Test <selftest@invalid>
Build-Depends: debhelper-compat (= 13)

Package: selftest-src
Architecture: all
Maintainer: Someone Else <else@invalid>
Description: fixture
"""
PLACEHOLDER = """\
selftest-src (0.0) unstable; urgency=medium

  * Placeholder changelog.

 -- Self Test <selftest@invalid>  Thu, 01 Jan 1970 00:00:00 +0000
"""


class Suffixes(unittest.TestCase):
    def test_table(self):
        for suite, pr, want in [
            ("bookworm", None, "0.3.post134~deb12"),
            ("trixie", None, "0.3.post134~deb13"),
            ("forky", None, "0.3.post134~deb14"),
            ("sid", None, "0.3.post134"),
            ("raspbian-trixie", None, "0.3.post134~deb13"),
            ("trixie", 41, "0.3.post134~deb13~pr41"),
            ("sid", 41, "0.3.post134~pr41"),
        ]:
            with self.subTest(suite=suite, pr=pr):
                self.assertEqual(dv.with_suffixes("0.3.post134", suite, pr), want)

    def test_unknown_suite_fails(self):
        with self.assertRaises(SystemExit):
            dv.with_suffixes("0.3", "buster", None)


@unittest.skipUnless(shutil.which("dpkg"), "needs dpkg --compare-versions")
class Ordering(unittest.TestCase):
    # docs/packaging.md's example, lowest first.
    ORDER = ["0.3.post134~deb12", "0.3.post134~deb13~pr41", "0.3.post134~deb13",
             "0.3.post134~deb14", "0.3.post134", "0.3.post135~deb12"]

    def test_order(self):
        for lower, higher in zip(self.ORDER, self.ORDER[1:]):
            with self.subTest(lower=lower, higher=higher):
                subprocess.run(["dpkg", "--compare-versions", lower, "lt", higher], check=True)


@unittest.skipUnless(shutil.which("git"), "needs git")
class Tree(unittest.TestCase):
    """A throwaway source tree with history, run through the script's CLI."""

    def setUp(self):
        self.src = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.src)
        (self.src / "debian").mkdir()
        (self.src / "debian/control").write_text(CONTROL)
        (self.src / "debian/changelog").write_text(PLACEHOLDER)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@invalid",
               "GIT_COMMITTER_DATE": "2026-09-24T12:00:00+0000"}
        self.env = {**os.environ, **env}
        self.env.pop("GITHUB_REPOSITORY", None)
        self.git("init", "-q", "-b", "main")
        self.git("remote", "add", "origin", "https://github.com/example/selftest-src.git")
        self.commit("one")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.src), *args], env=self.env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)

    def run_script(self, *args, check=True):
        return subprocess.run(["python3", str(SCRIPT), "--source-dir", str(self.src), *args],
                              env=self.env, check=check, capture_output=True, text=True)

    def test_no_tag_counts_every_commit(self):
        self.commit("two")
        self.assertEqual(self.run_script("--suite", "trixie").stdout.strip(), "0.0.post2~deb13")

    def test_at_the_tag_and_after(self):
        self.git("tag", "-a", "v0.3", "-m", "v0.3")
        self.assertEqual(self.run_script("--suite", "sid").stdout.strip(), "0.3")
        self.commit("two")
        self.assertEqual(self.run_script("--suite", "sid", "--pr", "7").stdout.strip(), "0.3.post1~pr7")

    def test_changelog_entry(self):
        sha = self.git("rev-parse", "HEAD")
        self.run_script("--suite", "trixie", "--write-changelog")
        text = (self.src / "debian/changelog").read_text()
        self.assertEqual(text, (
            "selftest-src (0.0.post1~deb13) trixie; urgency=medium\n\n"
            f"  * Built from example/selftest-src@{sha}\n\n"
            " -- Self Test <selftest@invalid>  Thu, 24 Sep 2026 12:00:00 +0000\n\n"
            + PLACEHOLDER))
        # Running it again replaces the entry instead of stacking a second.
        self.run_script("--suite", "trixie", "--write-changelog")
        self.assertEqual((self.src / "debian/changelog").read_text(), text)

    def test_committed_build_entry_is_refused(self):
        self.run_script("--suite", "trixie", "--write-changelog")
        self.commit("oops: committed a build's changelog")
        r = self.run_script("--suite", "trixie", "--write-changelog", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("generated entry", r.stderr)

    def test_shallow_clone_is_refused(self):
        self.commit("two")
        clone = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, clone)
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.src}", str(clone / "c")],
                       check=True, env=self.env, capture_output=True)
        r = subprocess.run(["python3", str(SCRIPT), "--source-dir", str(clone / "c"), "--suite", "sid"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("shallow", r.stderr)


if __name__ == "__main__":
    unittest.main()
