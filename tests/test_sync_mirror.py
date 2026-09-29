"""Tests for scripts/sync-mirror.py (docs/packaging.md, "Mirrors"), against
local git repositories: an "upstream", and "ours" with an orphan packaging
branch, one of our own branches, and packaging's v0.0 tag.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/sync-mirror.py"
_loader = importlib.machinery.SourceFileLoader("sync_mirror", str(SCRIPT))
sm = importlib.util.module_from_spec(importlib.util.spec_from_loader("sync_mirror", _loader))
_loader.exec_module(sm)

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


class SyncMirror(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        # upstream: master, dev and a tag.
        self.up = t / "up"
        git(t, "init", "-q", "-b", "master", str(self.up))
        self.commit(self.up, "one")
        git(self.up, "tag", "0.9.2")
        git(self.up, "checkout", "-q", "-b", "dev")
        self.commit(self.up, "dev work")
        git(self.up, "checkout", "-q", "master")
        # ours: a bare repository with an orphan packaging (tagged v0.0) and
        # github-master, one of our own branches.
        self.ours = t / "ours.git"
        git(t, "init", "-q", "--bare", "-b", "packaging", str(self.ours))
        seed = t / "seed"
        git(t, "init", "-q", "-b", "packaging", str(seed))
        self.commit(seed, "packaging")
        git(seed, "tag", "v0.0")
        git(seed, "checkout", "-q", "--orphan", "github-master")
        self.commit(seed, "archived")
        git(seed, "push", "-q", str(self.ours), "packaging", "github-master", "v0.0")
        # The clone the sync runs in, as the workflow's checkout.
        self.work = t / "work"
        git(t, "clone", "-q", str(self.ours), str(self.work))
        self.decl = t / "apt-packaging.toml"
        self.decl.write_text(f'kind = "mirror"\nupstream = "{self.up}"\n'
                             '[mirror]\nbuild = "master"\nours = ["github-master"]\n')

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, repo, msg, name="f"):
        (Path(repo) / name).write_text(msg + "\n")
        git(repo, "add", name)
        git(repo, "commit", "-q", "-m", msg)

    def sync(self, dry_run=False):
        cwd = os.getcwd()
        os.chdir(self.work)
        old = dict(os.environ)
        os.environ.update(ENV)
        self.summary = Path(self.tmp.name) / "summary.md"
        os.environ["GITHUB_STEP_SUMMARY"] = str(self.summary)
        try:
            upstream, build, ours = sm.load(self.decl)
            return sm.sync(upstream, build, ours, "origin", "packaging", dry_run)
        finally:
            os.environ.clear()
            os.environ.update(old)
            os.chdir(cwd)

    def here(self):
        return sm.refs(str(self.ours))

    def test_first_sync_copies_everything_and_builds(self):
        self.assertTrue(self.sync())
        up, here = sm.refs(str(self.up)), self.here()
        for n in ("refs/heads/master", "refs/heads/dev", "refs/tags/0.9.2"):
            self.assertEqual(here[n], up[n], n)
        # Ours untouched.
        self.assertIn("refs/heads/packaging", here)
        self.assertIn("refs/tags/v0.0", here)

    def test_nothing_new_nothing_built(self):
        self.sync()
        self.assertFalse(self.sync())

    def test_only_the_build_branch_builds(self):
        self.sync()
        git(self.up, "checkout", "-q", "dev")
        self.commit(self.up, "more dev")
        self.assertFalse(self.sync())
        self.assertEqual(self.here()["refs/heads/dev"], sm.refs(str(self.up))["refs/heads/dev"])
        git(self.up, "checkout", "-q", "master")
        self.commit(self.up, "two")
        self.assertTrue(self.sync())

    def test_upstream_rewrites_history(self):
        self.sync()
        git(self.up, "commit", "-q", "--amend", "-m", "rewritten")
        self.assertTrue(self.sync())
        self.assertEqual(self.here()["refs/heads/master"], sm.refs(str(self.up))["refs/heads/master"])
        self.assertIn("upstream rewrote master", self.summary.read_text())

    def test_fast_forward_is_not_a_rewrite(self):
        self.sync()
        self.commit(self.up, "two")
        self.assertTrue(self.sync())
        self.assertFalse(self.summary.exists() and "rewrote" in self.summary.read_text())

    def test_a_refused_tag_does_not_stop_the_branches(self):
        # As a tag ruleset refusing upstream's names: the remote's update hook.
        hook = self.ours / "hooks/update"
        hook.write_text('#!/bin/sh\ncase "$1" in refs/tags/0.9*) echo "refused by ruleset" >&2; exit 1 ;; esac\n')
        hook.chmod(0o755)
        git(self.up, "tag", "1.0")
        self.assertTrue(self.sync())
        here, up = self.here(), sm.refs(str(self.up))
        self.assertEqual(here["refs/heads/master"], up["refs/heads/master"])
        self.assertEqual(here["refs/tags/1.0"], up["refs/tags/1.0"])
        self.assertNotIn("refs/tags/0.9.2", here)
        self.assertIn("refused refs/tags/0.9.2", self.summary.read_text())

    def test_nothing_is_deleted(self):
        self.sync()
        git(self.up, "branch", "-q", "-D", "dev")
        self.assertFalse(self.sync())
        self.assertIn("refs/heads/dev", self.here())

    def test_our_branches_are_never_touched(self):
        before = self.here()
        for b in ("packaging", "github-master"):
            git(self.up, "branch", "-q", b)
        self.sync()
        after = self.here()
        for b in ("refs/heads/packaging", "refs/heads/github-master"):
            self.assertEqual(after[b], before[b], b)

    def test_our_tag_is_never_overwritten(self):
        git(self.up, "tag", "v0.0")  # upstream happens to have one of the same name
        self.sync()
        self.assertNotEqual(self.here()["refs/tags/v0.0"], sm.refs(str(self.up))["refs/tags/v0.0"])
        self.assertIn("upstream has refs/tags/v0.0, which is one of ours here", self.summary.read_text())

    def test_our_tag_from_a_shallow_checkout(self):
        # As actions/checkout leaves it: one commit deep, packaging's v0.0 a
        # commit further back.
        self.commit(self.work, "packaging, later")
        git(self.work, "push", "-q", "origin", "packaging")
        shallow = Path(self.tmp.name) / "shallow"
        git(self.tmp.name, "clone", "-q", "--depth", "1", f"file://{self.ours}", str(shallow))
        self.work = shallow
        git(self.up, "tag", "v0.0")
        self.sync()
        self.assertNotEqual(self.here()["refs/tags/v0.0"], sm.refs(str(self.up))["refs/tags/v0.0"])

    def test_a_copied_tag_follows_upstream(self):
        self.sync()
        git(self.up, "tag", "-f", "0.9.2", "dev")
        self.sync()
        self.assertEqual(self.here()["refs/tags/0.9.2"], sm.refs(str(self.up))["refs/tags/0.9.2"])

    def test_dry_run_pushes_nothing(self):
        before = self.here()
        self.assertTrue(self.sync(dry_run=True))
        self.assertEqual(self.here(), before)

    def test_declaration(self):
        self.decl.write_text('kind = "B"\n')
        with self.assertRaisesRegex(sm.Error, 'not "mirror"'):
            sm.load(self.decl)
        self.decl.write_text(f'kind = "mirror"\nupstream = "{self.up}"\n')
        with self.assertRaisesRegex(sm.Error, r"\[mirror\] build"):
            sm.load(self.decl)

    def test_no_build_branch_upstream(self):
        self.decl.write_text(f'kind = "mirror"\nupstream = "{self.up}"\n[mirror]\nbuild = "main"\n')
        with self.assertRaisesRegex(sm.Error, "has no main branch"):
            self.sync()


if __name__ == "__main__":
    unittest.main()
