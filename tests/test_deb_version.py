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


class PatchSeries(unittest.TestCase):
    """<upstream version>+<owner-tag>.<X.Y.postN>, and the epoch."""

    def test_form(self):
        for upstream, epoch, want in [
            ("1.1.1.post173", None, "1.1.1.post173+fpgasonline.0.0.post70"),
            ("1.1.1", None, "1.1.1+fpgasonline.0.0.post70"),
            ("20260914", None, "20260914+fpgasonline.0.0.post70"),
            ("11.1.0", 2, "2:11.1.0+fpgasonline.0.0.post70"),
        ]:
            with self.subTest(upstream=upstream, epoch=epoch):
                self.assertEqual(dv.package_version("0.0.post70", upstream, "fpgasonline", epoch), want)

    def test_set_b_is_unchanged(self):
        self.assertEqual(dv.package_version("0.3.post134", None, None, None), "0.3.post134")
        self.assertEqual(dv.package_version("0.1.post5", None, None, 2), "2:0.1.post5")

    def test_upstream_describe(self):
        for describe, want in [
            ("v1.1.1-173-g24e46d1", "1.1.1.post173"),
            ("v11.1.0-0-g0123abc", "11.1.0"),
            ("v0.12.0-1701-gabcdef0", "0.12.0.post1701"),
            ("v11.0.0-rc2-5-gabcdef0", "11.0.0~rc2.post5"),
            ("1.2_3-0-gabcdef0", "1.2.3"),
        ]:
            with self.subTest(describe=describe):
                self.assertEqual(dv.upstream_from_describe(describe), want)

    def test_bad_upstream_fails(self):
        for bad in ["openocd-0.12-0-gabcdef0", "v1.0:2-0-gabcdef0"]:
            with self.subTest(describe=bad), self.assertRaises(SystemExit):
                dv.upstream_from_describe(bad)
        for bad in ["", "v1.0", "1.0-1", "1:1.0", "1.0 beta"]:
            with self.subTest(upstream=bad), self.assertRaises(SystemExit):
                dv.package_version("0.0.post1", bad, "fpgasonline", None)

    def test_bad_owner_tag_and_epoch_fail(self):
        for tag in ["", "FPGAs", "fpgas.online", "welland1"]:
            with self.subTest(tag=tag), self.assertRaises(SystemExit):
                dv.package_version("0.0.post1", "1.0", tag, None)
        with self.assertRaises(SystemExit):
            dv.package_version("0.0.post1", "1.0", "fpgasonline", 0)


@unittest.skipUnless(shutil.which("dpkg"), "needs dpkg --compare-versions")
class Ordering(unittest.TestCase):
    # docs/packaging.md's example, lowest first.
    ORDER = ["0.3.post134~deb12", "0.3.post134~deb13~pr41", "0.3.post134~deb13",
             "0.3.post134~deb14", "0.3.post134", "0.3.post135~deb12"]

    # A patch series: a preview below its main build, each suite below the
    # next and sid on top; a commit here raises every suite; an upstream bump
    # beats any number of commits here. The first entry is what
    # fpgas.online-fpga-tools published before the shared script.
    PATCH_SERIES = [
        "1.1.1+fpgasonline.0.0.post78",
        "1.1.1+fpgasonline.0.0.post79~deb12~pr5",
        "1.1.1+fpgasonline.0.0.post79~deb12",
        "1.1.1+fpgasonline.0.0.post79~deb13",
        "1.1.1+fpgasonline.0.0.post79~deb14",
        "1.1.1+fpgasonline.0.0.post79",
        "1.1.1+fpgasonline.0.0.post80~deb12",
        "1.1.1.post1+fpgasonline.0.0.post1~deb12",
        "1.1.2~rc1+fpgasonline.0.0.post1",
        "1.1.2+fpgasonline.0.0.post1~deb12",
    ]
    # An nfpm build (Go) is plain Set B, without debian/: the first entry is
    # what go-claude-teleport published from its tag, the next its first
    # shared-script build, one commit later.
    NFPM = ["0.24", "0.24.post1~deb13~pr9", "0.24.post1~deb13", "0.24.post1~deb14",
            "0.24.post1", "0.24.post2~deb13", "0.25~deb13"]
    # rpi-qemu keeps its epoch (a declared PKG-VERSION exception).
    EPOCH = ["2:0.1+112.g91e6fbe", "2:11.1.0+fpgasonline.0.1.post113~deb13",
             "2:11.1.0+fpgasonline.0.1.post113"]

    def test_order(self):
        for name in ["ORDER", "PATCH_SERIES", "NFPM", "EPOCH"]:
            table = getattr(self, name)
            for lower, higher in zip(table, table[1:]):
                with self.subTest(table=name, lower=lower, higher=higher):
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

    def test_generated_tags_are_not_releases(self):
        # rpi-qemu's old release job tagged every build v0.1.<N>.g<sha>; only
        # vX.Y and vX.Y.Z are releases.
        self.git("tag", "-a", "v0.1", "-m", "v0.1")
        self.commit("two")
        self.git("tag", "v0.1.1.gdeadbee")
        self.assertEqual(self.run_script("--suite", "sid").stdout.strip(), "0.1.post1")

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

    def test_no_committed_changelog(self):
        # Set B commits no debian/changelog and ignores it (docs/packaging.md,
        # "The changelog"): the build's entry is the whole file.
        (self.src / "debian/changelog").unlink()
        (self.src / ".gitignore").write_text("debian/changelog\n")
        self.commit("two: no committed changelog")
        sha = self.git("rev-parse", "HEAD")
        want = ("selftest-src (0.0.post2~deb13) trixie; urgency=medium\n\n"
                f"  * Built from example/selftest-src@{sha}\n\n"
                " -- Self Test <selftest@invalid>  Thu, 24 Sep 2026 12:00:00 +0000\n")
        self.run_script("--suite", "trixie", "--write-changelog")
        self.assertEqual((self.src / "debian/changelog").read_text(), want)
        # Again, over the file the first run left: still exactly one entry.
        self.run_script("--suite", "trixie", "--write-changelog")
        self.assertEqual((self.src / "debian/changelog").read_text(), want)
        # And the ignored file leaves the tree clean.
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_committed_build_entry_is_refused(self):
        self.run_script("--suite", "trixie", "--write-changelog")
        self.commit("oops: committed a build's changelog")
        r = self.run_script("--suite", "trixie", "--write-changelog", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("generated entry", r.stderr)

    def upstream_tree(self, tag, after):
        """A fetched upstream: its own git repository, tagged, `after` commits past the tag."""
        up = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, up)
        g = lambda *a: subprocess.run(["git", "-C", str(up), *a], env=self.env, check=True,
                                      capture_output=True, text=True)
        g("init", "-q", "-b", "master")
        g("commit", "-q", "--allow-empty", "-m", "release")
        g("tag", "-a", tag, "-m", tag)
        for i in range(after):
            g("commit", "-q", "--allow-empty", "-m", f"upstream {i}")
        return up

    def test_patch_series_from_upstream_dir(self):
        up = self.upstream_tree("v1.1.1", 3)
        self.commit("two")
        r = self.run_script("--suite", "trixie", "--upstream-dir", str(up), "--owner-tag", "fpgasonline")
        self.assertEqual(r.stdout.strip(), "1.1.1.post3+fpgasonline.0.0.post2~deb13")

    def test_patch_series_from_upstream_version(self):
        r = self.run_script("--suite", "sid", "--pr", "4", "--upstream-version", "20260914",
                            "--owner-tag", "fpgasonline")
        self.assertEqual(r.stdout.strip(), "20260914+fpgasonline.0.0.post1~pr4")

    def test_patch_series_needs_both(self):
        for args in [["--upstream-version", "1.0"], ["--owner-tag", "fpgasonline"],
                     ["--upstream-version", "1.0", "--upstream-dir", ".", "--owner-tag", "x"]]:
            with self.subTest(args=args):
                r = self.run_script("--suite", "sid", *args, check=False)
                self.assertNotEqual(r.returncode, 0)

    def test_shallow_upstream(self):
        # A shallow clone at the pinned tag (rpi-qemu's `git clone --depth=1
        # --branch v11.1.0`) describes exactly; past the tag it can't count.
        up = self.upstream_tree("v11.1.0", 0)
        clone = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, clone)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "v11.1.0",
                        f"file://{up}", str(clone / "at-tag")], check=True, env=self.env,
                       capture_output=True)
        r = self.run_script("--suite", "trixie", "--upstream-dir", str(clone / "at-tag"),
                            "--owner-tag", "fpgasonline", "--epoch", "2")
        self.assertEqual(r.stdout.strip(), "2:11.1.0+fpgasonline.0.0.post1~deb13")
        up2 = self.upstream_tree("v1.0", 2)
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{up2}", str(clone / "past")],
                       check=True, env=self.env, capture_output=True)
        r = self.run_script("--suite", "trixie", "--upstream-dir", str(clone / "past"),
                            "--owner-tag", "fpgasonline", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("shallow", r.stderr)

    def test_version_tree(self):
        # The debian/ tree is the fetched upstream's checkout; the version and
        # the "Built from" commit are this repository's.
        up = self.upstream_tree("v1.1.1", 0)
        (up / "debian").mkdir()
        (up / "debian/control").write_text(CONTROL)
        self.commit("two")
        sha = self.git("rev-parse", "HEAD")
        r = subprocess.run(["python3", str(SCRIPT), "--source-dir", str(up), "--version-tree",
                            str(self.src), "--suite", "bookworm", "--upstream-dir", str(up),
                            "--owner-tag", "fpgasonline", "--write-changelog"],
                           env=self.env, check=True, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "1.1.1+fpgasonline.0.0.post2~deb12")
        self.assertEqual((up / "debian/changelog").read_text(), (
            "selftest-src (1.1.1+fpgasonline.0.0.post2~deb12) bookworm; urgency=medium\n\n"
            f"  * Built from example/selftest-src@{sha}\n\n"
            " -- Self Test <selftest@invalid>  Thu, 24 Sep 2026 12:00:00 +0000\n"))

    def test_nfpm_tree_has_no_debian(self):
        # A Go repository packaged with nfpm has no debian/: printing the
        # version needs none, writing a changelog does.
        self.git("rm", "-q", "-r", "debian")
        self.commit("two: no debian/")
        self.git("tag", "-a", "v0.24", "-m", "v0.24", "HEAD~1")
        self.assertEqual(self.run_script("--suite", "trixie").stdout.strip(), "0.24.post1~deb13")
        r = self.run_script("--suite", "trixie", "--write-changelog", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("debian/control", r.stderr)

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
