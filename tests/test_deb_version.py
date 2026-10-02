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

    def test_debian_source_form(self):
        # A Debian source package's own version, revision included, then ours.
        for upstream, want in [
            ("43.0.0-3+deb13u1", "43.0.0-3+deb13u1+welland.0.0.post6"),
            ("49.0.0-2", "49.0.0-2+welland.0.0.post6"),
            ("1.2-3-4", "1.2-3-4+welland.0.0.post6"),   # an upstream part with a -
        ]:
            with self.subTest(upstream=upstream):
                self.assertEqual(dv.package_version("0.0.post6", upstream, "welland", None, True),
                                 want)

    def test_bad_debian_source_fails(self):
        for bad in ["43.0.0", "1:43.0.0-3", "43.0.0-", "v43.0.0-3", "43.0.0-3 x"]:
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    dv.package_version("0.0.post6", bad, "welland", None, True)

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

    def test_upstream_describe_with_a_match(self):
        # A tag chosen by --upstream-tag-match loses a project-name prefix too;
        # without one, the result is exactly as before (openocd-0.12 fails).
        for describe, want in [
            ("netplan-1.1.2-4-gabcdef0", "1.1.2.post4"),
            ("migen_0.9.2-0-gabcdef0", "0.9.2"),
            ("v1.1.1-173-g24e46d1", "1.1.1.post173"),
            ("0.9.2-126-gbeffe83", "0.9.2.post126"),
        ]:
            with self.subTest(describe=describe):
                self.assertEqual(dv.upstream_from_describe(describe, strip_prefix=True), want)

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

    # A Debian source package rebuilt with our changes (cryptography-insecure):
    # the first entry of each is what paramiko-insecure published it as before
    # the split; a Debian stable update, or a new Debian version, beats any
    # number of our commits.
    DEBIAN_SOURCE = [
        "43.0.0-3+deb13u1+insecure1",
        "43.0.0-3+deb13u1+welland.0.0.post6~deb13~pr1",
        "43.0.0-3+deb13u1+welland.0.0.post6~deb13",
        "43.0.0-3+deb13u1+welland.0.0.post7~deb13",
        "43.0.0-3+deb13u2+welland.0.0.post7~deb13",
        "43.0.0-4+welland.0.0.post1~deb13",
    ]
    BOOKWORM = ["38.0.4-3+deb12u1+insecure1", "38.0.4-3+deb12u1+welland.0.0.post6~deb12"]
    # A Debian binNMU of the same source sorts below ours (b < w): it doesn't
    # replace our build, and our pin needs no change for it.
    BINNMU = ["49.0.0-2+b1", "49.0.0-2+welland.0.0.post6"]
    SID = ["49.0.0-2+insecure1", "49.0.0-2+welland.0.0.post6~deb14",
           "49.0.0-2+welland.0.0.post6"]

    def test_order(self):
        for name in ["ORDER", "PATCH_SERIES", "NFPM", "EPOCH", "DEBIAN_SOURCE", "BOOKWORM", "SID",
                     "BINNMU"]:
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

    def test_upstream_tag_match(self):
        # upstream's newest tag is a packaging tag; only 0.9.2 is a version.
        up = self.upstream_tree("0.9.2", 2)
        subprocess.run(["git", "-C", str(up), "tag", "debian/0.9.2-1"], check=True, env=self.env)
        self.commit("two")
        base = ["--suite", "sid", "--upstream-dir", str(up), "--owner-tag", "fpgasonline"]
        # Any tag (the default, unchanged): the packaging tag is taken, and refused.
        self.assertNotEqual(self.run_script(*base, check=False).returncode, 0)
        r = self.run_script(*base, "--upstream-tag-match", "[0-9]*")
        self.assertEqual(r.stdout.strip(), "0.9.2.post2+fpgasonline.0.0.post2")

    def test_mirror_declaration_gives_the_match(self):
        up = self.upstream_tree("0.9.2", 1)
        subprocess.run(["git", "-C", str(up), "tag", "experiment/x"], check=True, env=self.env)
        (self.src / ".github").mkdir()
        decl = self.src / ".github/apt-packaging.toml"
        base = ["--suite", "sid", "--upstream-dir", str(up), "--owner-tag", "fpgasonline"]
        decl.write_text('kind = "mirror"\nupstream = "https://example.org/x.git"\n[mirror]\nbuild = "master"\n')
        self.assertEqual(self.run_script(*base).stdout.strip(), "0.9.2.post1+fpgasonline.0.0.post1")
        decl.write_text(decl.read_text() + 'tags = "v[0-9]*"\n')
        self.assertNotEqual(self.run_script(*base, check=False).returncode, 0)  # no v tags
        # Not a mirror: any tag, as before.
        decl.write_text('kind = "B"\nvariant = "patch-series"\n')
        self.assertNotEqual(self.run_script(*base, check=False).returncode, 0)

    def test_upstream_tag_match_needs_upstream_dir(self):
        r = self.run_script("--suite", "sid", "--upstream-version", "1.0", "--owner-tag", "x",
                            "--upstream-tag-match", "[0-9]*", check=False)
        self.assertNotEqual(r.returncode, 0)

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

    def test_debian_source_keeps_its_changelog(self):
        # A Debian source fetched at build time, not a git checkout: Debian's
        # changelog stays under our entry, and a second run is refused.
        fetched = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, fetched)
        (fetched / "debian").mkdir()
        (fetched / "debian/control").write_text(CONTROL)
        debian = PLACEHOLDER.replace("selftest-src (0.0)", "selftest-src (43.0.0-3+deb13u1)")
        (fetched / "debian/changelog").write_text(debian)
        self.commit("two")
        sha = self.git("rev-parse", "HEAD")
        args = ["python3", str(SCRIPT), "--source-dir", str(fetched), "--version-tree",
                str(self.src), "--suite", "trixie", "--upstream-debian-version",
                "43.0.0-3+deb13u1", "--owner-tag", "welland", "--write-changelog"]
        r = subprocess.run(args, env=self.env, check=True, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "43.0.0-3+deb13u1+welland.0.0.post2~deb13")
        self.assertEqual((fetched / "debian/changelog").read_text(), (
            "selftest-src (43.0.0-3+deb13u1+welland.0.0.post2~deb13) trixie; urgency=medium\n\n"
            f"  * Built from example/selftest-src@{sha}\n\n"
            " -- Self Test <selftest@invalid>  Thu, 24 Sep 2026 12:00:00 +0000\n\n"
            + debian))
        again = subprocess.run(args, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("generated entry", again.stderr)

    def test_debian_source_must_be_the_pinned_version(self):
        # The pin says one Debian version and the fetched tree is another (a
        # stable update fetched under an old pin): refused, not built as the
        # pin's version with the other's code.
        fetched = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, fetched)
        (fetched / "debian").mkdir()
        (fetched / "debian/control").write_text(CONTROL)
        (fetched / "debian/changelog").write_text(
            PLACEHOLDER.replace("selftest-src (0.0)", "selftest-src (43.0.0-3+deb13u2)"))
        r = subprocess.run(["python3", str(SCRIPT), "--source-dir", str(fetched), "--version-tree",
                            str(self.src), "--suite", "trixie", "--upstream-debian-version",
                            "43.0.0-3+deb13u1", "--owner-tag", "welland", "--write-changelog"],
                           env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("43.0.0-3+deb13u2, not --upstream-debian-version 43.0.0-3+deb13u1", r.stderr)

    def test_debian_source_with_an_epoch(self):
        # A Debian source with an epoch: its changelog says 1:2.3-1, the flag
        # takes 2.3-1 and the epoch goes in --epoch. They match only when our
        # epoch is Debian's.
        fetched = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, fetched)
        (fetched / "debian").mkdir()
        (fetched / "debian/control").write_text(CONTROL)
        debian = PLACEHOLDER.replace("selftest-src (0.0)", "selftest-src (1:2.3-1)")

        def run(*epoch):
            (fetched / "debian/changelog").write_text(debian)
            return subprocess.run(["python3", str(SCRIPT), "--source-dir", str(fetched),
                                   "--version-tree", str(self.src), "--suite", "trixie",
                                   "--upstream-debian-version", "2.3-1", *epoch,
                                   "--owner-tag", "welland", "--write-changelog"],
                                  env=self.env, capture_output=True, text=True)

        r = run("--epoch", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "1:2.3-1+welland.0.0.post1~deb13")
        for epoch in [[], ["--epoch", "2"]]:
            with self.subTest(epoch=epoch):
                r = run(*epoch)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("is for 1:2.3-1, not --upstream-debian-version", r.stderr)

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


class SetA(unittest.TestCase):
    """<base>+<owner-tag><M> (docs/packaging.md, "Set A")."""

    def test_release_tags_are_normalised(self):
        # A leading v or project-name prefix goes; - and _ become dots.
        for tag, want in [
            ("v2.93", "2.93"),
            ("2.93", "2.93"),
            ("netplan-1.1.2", "1.1.2"),
            ("RELEASE_7_5", "7.5"),
            ("RELEASE_6_0_1", "6.0.1"),
            ("7.5", "7.5"),                 # a release commit's subject gives it plain
            ("V1.0", "1.0"),
            ("3.5a", "3.5a"),               # tmux's letter is a later release, not a pre-release
            ("usdr2-0.9.9", "0.9.9"),
            ("rel_1_2_3", "1.2.3"),
            ("my-project-1.2", "1.2"),      # a prefix with a hyphen of its own
            ("some_tool_v2-0.4", "0.4"),
            # A pre-release gets a ~, so it sorts below its release: with a
            # hyphen, straight after the digit, or after a dot.
            ("3.8-rc3", "3.8~rc3"),
            ("v1.0-rc1", "1.0~rc1"),
            ("v2.94rc1", "2.94~rc1"),
            ("v2.94test1", "2.94~test1"),
            ("v1.0RC2", "1.0~RC2"),
            ("2.0alpha1", "2.0~alpha1"),
            ("2.0-beta2", "2.0~beta2"),
            ("2.0pre3", "2.0~pre3"),
            ("v1.0.rc1", "1.0~rc1"),
            ("v1.0_rc1", "1.0~rc1"),
            ("tmux-3.8-rc3", "3.8~rc3"),
        ]:
            with self.subTest(tag=tag):
                self.assertEqual(dv.release_version(tag), want)

    def test_bad_release_tags_fail(self):
        for tag in ["debian/2.90-1", "release", "v", "1.0:2", "nightly", "1.0 beta",
                    # A hyphen before a digit: a date, or a revision. As ~ it
                    # would sort below the version before it (2024~01~15 < 2024).
                    "release-2024-01-15", "2024-01-15", "v1.2-3", "1.0-rc-2"]:
            with self.subTest(tag=tag), self.assertRaises(SystemExit):
                dv.release_version(tag)

    def test_the_other_forms_normalise_as_before(self):
        # A mirror's or a patch series' upstream version is not Set A's: no ~
        # is added to an unhyphenated pre-release there, and a hyphen before
        # a digit becomes ~ as it always has.
        for describe, strip, want in [
            ("v2.94rc1-3-gabcdef0", True, "2.94rc1.post3"),
            ("v1.2-3-0-gabcdef0", False, "1.2~3"),
            ("v11.0.0-rc2-5-gabcdef0", False, "11.0.0~rc2.post5"),
            ("my-project-1.2-0-gabcdef0", True, "1.2"),   # refused before; never another version
        ]:
            with self.subTest(describe=describe):
                self.assertEqual(dv.upstream_from_describe(describe, strip_prefix=strip), want)

    def test_debian_base(self):
        # Debian's version is the base only at the release it is for.
        for debian, release, n, want in [
            ("1.1.2-7", "1.1.2", 0, "1.1.2-7"),
            ("1.1.2-7", "1.1.2", 3, None),              # upstream is past it
            ("2.93+dfsg-1", "2.93", 0, "2.93+dfsg-1"),  # Debian's repack of the release
            ("2.93+dfsg1+ds-2", "2.93", 0, "2.93+dfsg1+ds-2"),
            ("2.93+ds1-1", "2.93", 0, "2.93+ds1-1"),
            ("2.93+ds1-1", "2.93", 4, None),
            ("2.93-4+deb13u1", "2.93", 0, "2.93-4+deb13u1"),
            ("2.92-4", "2.93", 0, None),                # another release's debian/
            ("2.93~rc1-1", "2.93", 0, None),
            ("2.93~dfsg-1", "2.93", 0, None),           # sorts below 2.93-0: ours is above it anyway
            ("2.930-1", "2.93", 0, None),
            ("2.93.1-1", "2.93", 0, None),
            ("2.93a-1", "2.93", 0, None),
            ("2.93", "2.93", 0, None),                  # native: no Debian revision
            (None, "2.93", 0, None),
        ]:
            with self.subTest(debian=debian, release=release, n=n):
                self.assertEqual(dv.debian_base(debian, release, n), want)

    def test_unsupported_repacks_fail(self):
        # They sort above <release>+git<N>: at the release ours would be
        # above its own next build, and after it Debian's would replace ours.
        for debian in ["2.0+repack-1", "2.0+really1.9-1", "2.0.ds1-1", "2.0.dfsg-1",
                       "2.0+git20240101-1"]:
            for n in (0, 5):
                with self.subTest(debian=debian, n=n), self.assertRaises(SystemExit):
                    dv.debian_base(debian, "2.0", n)

    def test_base(self):
        sha = "06489e0b9d7c1c7a52f6a5f0f1f2d3e4f5a6b7c8"
        for release, n, debian, want in [
            ("1.1.2", 0, "1.1.2-7", "1.1.2-7"),            # 1. Debian's version
            ("2.93", 0, None, "2.93-0"),                   # 2. exactly at a release
            ("7.5", 583, None, "7.5+git583.g06489e0-0"),   # 3. N commits after one
            ("7.5", 1, None, "7.5+git1.g06489e0-0"),
            (None, 6233, None, "0.0+git6233.g06489e0-0"),  # 4. upstream has no tags
        ]:
            with self.subTest(release=release, n=n, debian=debian):
                self.assertEqual(dv.set_a_base(release, n, sha, debian), want)


@unittest.skipUnless(shutil.which("dpkg"), "needs dpkg --compare-versions")
class SetAOrdering(unittest.TestCase):
    """Every ordering docs/packaging.md states for a Set A version, lowest first."""

    # The suites and the preview, as for any version: a preview below its
    # default-branch build, each suite below the next, sid on top, and the
    # next push (one more commit on packaging) above them all. The first entry
    # is Debian's own build of that release.
    SUITES = [
        "7.5-2",
        "7.5+git583.g06489e0-0+welland10~deb12",
        "7.5+git583.g06489e0-0+welland10~deb13~pr41",
        "7.5+git583.g06489e0-0+welland10~deb13",
        "7.5+git583.g06489e0-0+welland10~deb14",
        "7.5+git583.g06489e0-0+welland10",
        "7.5+git583.g06489e0-0+welland11~deb12",
    ]
    # Upstream moving: the release, commits after it (counted as numbers, so
    # 9 is below 10), a merge of upstream above any number of our commits,
    # and the next release above every snapshot of the last.
    UPSTREAM = [
        "0.0+git6233.g06489e0-0+welland10",     # upstream without tags
        "2.93-0+welland3~deb13",
        "2.93-0+welland4~deb13",
        "2.93+git1.g24e46d1-0+welland4~deb13",
        "2.93+git9.g0123abc-0+welland99~deb13",
        "2.93+git10.gabcdef0-0+welland1~deb13",
        "2.94-0+welland2~deb12",
    ]
    # debian/ from Debian: ours extends Debian's revision, so +<owner-tag>
    # sorts after Debian's own suffixes (a binNMU's +b1, a stable update's
    # +deb13u1: w and f both come after b and d), for either owner; and
    # Debian's next revision, or its next upstream version, replaces ours:
    # the signal to merge.
    DEBIAN = [
        "1.1.2-7",
        "1.1.2-7+b1",
        "1.1.2-7+deb13u1",
        "1.1.2-7+fpgasonline10~deb13",
        "1.1.2-7+welland10~deb13~pr5",
        "1.1.2-7+welland10~deb13",
        "1.1.2-7+welland10",
        "1.1.2-7+welland11~deb13",
        "1.1.2-8",
        "1.1.2+git4.gabcdef0-0+welland12~deb13",   # upstream merged past the release
        "1.1.3-1",
    ]
    # smartmontools: above Debian's 7.5-2 and what it published from its own
    # script, below a Debian 8.0-1.
    SMARTMONTOOLS = [
        "7.5-2",
        "7.5-2+welland1~deb13",                     # had it been built at the release
        "7.5+git583.g06489e0-0+welland4~deb13",
        "7.5+git583.g06489e0-0+welland10~deb13",
        "7.5+git583.g06489e0-0+welland11~deb13",
        "7.5+git601.g1234567-0+welland12~deb13",
        "8.0-0+welland13~deb13",
        "8.0-1",
    ]

    # Pre-releases, as release_version() normalises their tags (v2.94rc1,
    # v2.94test1): below their release, which is below the commits after it,
    # which are below the next release.
    RC = [
        "2.93+git40.gabcdef0-0+welland3~deb13",
        "2.94~rc1-0+welland3~deb13",
        "2.94~rc1+git2.g0123abc-0+welland3~deb13",
        "2.94~rc2-0+welland3~deb13",
        "2.94-0+welland3~deb13",
        "2.94+git1.g24e46d1-0+welland3~deb13",
        "2.95~rc1-0+welland3~deb13",
        "2.95-0+welland3~deb13",
    ]
    TEST = [
        "2.93+git40.gabcdef0-0+welland3~deb13",
        "2.94~test1-0+welland3~deb13",
        "2.94~test1+git2.g0123abc-0+welland3~deb13",
        "2.94~test2-0+welland3~deb13",
        "2.94-0+welland3~deb13",
        "2.94+git1.g24e46d1-0+welland3~deb13",
        "2.95-0+welland3~deb13",
    ]
    # Between pre-release words dpkg's order is the alphabet's: alpha, beta,
    # pre, rc, test. dnsmasq makes its test releases BEFORE its release
    # candidates, so there a build after 2.94test5 sorts above 2.94rc1's
    # until 2.94 itself. Nothing but upstream's own words is in the version.
    WORDS = ["2.94~alpha1-0+welland1", "2.94~beta1-0+welland1", "2.94~pre1-0+welland1",
             "2.94~rc1-0+welland1", "2.94~test1-0+welland1", "2.94-0+welland1"]
    # tmux: a build after its 3.8-rc3 tag sorts above what its own script
    # published as a dated snapshot before 3.8, and below 3.8.
    TMUX = [
        "3.7b",
        "3.8~git20260720.5ed5e36-0+welland1",
        "3.8~rc3-0+welland3~deb13",
        "3.8~rc3+git5.gabcdef0-0+welland3~deb13",
        "3.8-0+welland3~deb13",
        "3.8-1",
    ]
    # The committed changelog topped by an entry of ours on Debian's: the
    # base is still Debian's revision, never -0 (below Debian's 1.1.2-7).
    OURS_ON_DEBIANS = ["1.1.2-0+welland2", "1.1.2-7", "1.1.2-7+welland1", "1.1.2-7+welland2~deb13"]
    # Debian's +dfsg and +ds repacks sort below the +git<N> after the release.
    REPACK = [
        "2.93+dfsg-1",
        "2.93+dfsg-1+welland2~deb13",
        "2.93+ds1-1+welland2~deb13",
        "2.93+git1.gabcdef0-0+welland3~deb13",
        "2.94~rc1-0+welland4~deb13",
    ]
    # ... and why the others are refused: each sorts above the next build.
    REFUSED = [
        "2.0+git1.gabcdef0-0+welland3",
        "2.0+really1.9-1",
        "2.0+repack-1",
        "2.0.ds1-1",
    ]

    def test_order(self):
        for name in ["SUITES", "UPSTREAM", "DEBIAN", "SMARTMONTOOLS", "RC", "TEST", "WORDS", "TMUX",
                     "OURS_ON_DEBIANS", "REPACK", "REFUSED"]:
            table = getattr(self, name)
            for lower, higher in zip(table, table[1:]):
                with self.subTest(table=name, lower=lower, higher=higher):
                    subprocess.run(["dpkg", "--compare-versions", lower, "lt", higher], check=True)


@unittest.skipUnless(shutil.which("git"), "needs git")
class SetATree(unittest.TestCase):
    """A throwaway Set A repository: an `upstream` branch with upstream's
    history, and `packaging`, which is that plus debian/ and our commits."""

    SUBJECT = r"^Release (\d+(?:\.\d+)+) RELEASE_\d+(?:_\d+)+$"

    def setUp(self):
        self.src = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.src)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@invalid",
               "GIT_COMMITTER_DATE": "2026-09-24T12:00:00+0000"}
        self.env = {**os.environ, **env}
        self.env.pop("GITHUB_REPOSITORY", None)
        self.env.pop("GITHUB_ACTIONS", None)    # CI sets it: warnings are plain unless a test asks
        self.git("init", "-q", "-b", "upstream")
        self.git("remote", "add", "origin", "https://github.com/example/selftest-src.git")
        self.commit("upstream: one")
        self.commit("upstream: two")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.src), *args], env=self.env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)

    def packaging(self, commits=2, changelog=PLACEHOLDER):
        """Branch `packaging` off upstream: debian/, then our own commits."""
        self.git("checkout", "-q", "-b", "packaging")
        (self.src / "debian").mkdir()
        (self.src / "debian/control").write_text(CONTROL)
        if changelog:
            (self.src / "debian/changelog").write_text(changelog)
        self.commit("debian/")
        for i in range(1, commits):
            self.commit(f"ours {i}")

    def upstream_moves(self, *subjects, merge=True):
        """New commits on upstream, merged into packaging as sync-upstream does."""
        self.git("checkout", "-q", "upstream")
        for s in subjects:
            self.commit(s)
        self.git("checkout", "-q", "packaging")
        if merge:
            self.git("merge", "-q", "--no-ff", "-m", "Merge upstream", "upstream")

    def sha7(self, ref="upstream"):
        return self.git("rev-parse", ref)[:7]

    def run_script(self, *args, check=True, set_a=True):
        which = ["--owner-tag", "welland", "--upstream-branch", "upstream"] if set_a else []
        return subprocess.run(["python3", str(SCRIPT), "--source-dir", str(self.src), *which, *args],
                              env=self.env, check=check, capture_output=True, text=True)

    def version(self, *args):
        return self.run_script(*args).stdout.strip()

    def test_at_a_release(self):
        # N = 0: no +git, whatever the suite or the pull request.
        self.git("tag", "-a", "v2.93", "-m", "v2.93")
        self.packaging()
        for args, want in [
            (["--suite", "trixie"], "2.93-0+welland2~deb13"),
            (["--suite", "forky"], "2.93-0+welland2~deb14"),
            (["--suite", "raspbian-trixie"], "2.93-0+welland2~deb13"),
            (["--suite", "sid"], "2.93-0+welland2"),
            (["--suite", "trixie", "--pr", "41"], "2.93-0+welland2~deb13~pr41"),
            (["--suite", "sid", "--pr", "41"], "2.93-0+welland2~pr41"),
        ]:
            with self.subTest(args=args):
                self.assertEqual(self.version(*args), want)

    def test_after_a_release(self):
        # N upstream commits since the tag, the upstream commit's id, and M
        # of ours: none of N or the id changes with our commits.
        self.git("tag", "netplan-1.1.2", "HEAD~1")
        up = self.sha7()
        self.packaging(commits=3)
        self.assertEqual(self.version("--suite", "trixie"), f"1.1.2+git1.g{up}-0+welland3~deb13")
        self.assertEqual(self.version("--suite", "sid", "--pr", "7"), f"1.1.2+git1.g{up}-0+welland3~pr7")
        self.commit("ours, another")
        self.assertEqual(self.version("--suite", "sid"), f"1.1.2+git1.g{up}-0+welland4")

    def test_merging_upstream(self):
        # A merge of upstream raises N and changes the id; the merge commit
        # is one more of ours. Upstream moving on without a merge changes
        # nothing: the build is of the upstream commit packaging holds.
        self.git("tag", "v1.0", "HEAD~1")
        self.packaging()
        self.upstream_moves("upstream: three", "upstream: four")
        up = self.sha7()
        self.assertEqual(self.version("--suite", "sid"), f"1.0+git3.g{up}-0+welland3")
        self.upstream_moves("upstream: five", merge=False)
        self.assertEqual(self.version("--suite", "sid"), f"1.0+git3.g{up}-0+welland3")
        # ... and a new release, merged: back to no +git.
        self.git("tag", "v1.1", "upstream")
        self.git("merge", "-q", "--no-ff", "-m", "Merge upstream", "upstream")
        self.assertEqual(self.version("--suite", "sid"), "1.1-0+welland4")

    def test_upstream_without_tags(self):
        # 0.0, with N counting every upstream commit. It is also what a
        # checkout without upstream's tags gives, so it says so.
        up = self.sha7()
        self.packaging()
        r = self.run_script("--suite", "trixie")
        self.assertEqual(r.stdout.strip(), f"0.0+git2.g{up}-0+welland2~deb13")
        self.assertIn("deb-version.py: warning: no upstream release tag", r.stderr)
        self.assertIn("release-subject", r.stderr)
        self.assertNotIn("::warning", r.stderr)
        self.env["GITHUB_ACTIONS"] = "true"
        r = self.run_script("--suite", "trixie")
        self.assertEqual(r.stdout.strip(), f"0.0+git2.g{up}-0+welland2~deb13")   # stdout: only the version
        self.assertIn("::warning title=deb-version.py::no upstream release tag", r.stderr)

    def test_no_warning_with_a_release(self):
        self.git("tag", "v2.93")
        self.packaging()
        self.assertEqual(self.run_script("--suite", "sid").stderr, "")

    def test_pre_release_tags(self):
        # dnsmasq's v2.94rc1 and tmux's 3.8-rc3: a ~, so below the release.
        self.git("tag", "v2.94rc1", "HEAD~1")
        up = self.sha7()
        self.packaging()
        self.assertEqual(self.version("--suite", "sid"), f"2.94~rc1+git1.g{up}-0+welland2")
        self.git("tag", "3.8-rc3", "upstream")
        self.assertEqual(self.version("--suite", "trixie"), "3.8~rc3-0+welland2~deb13")

    def test_date_style_tag_fails(self):
        self.git("tag", "release-2024-01-15")
        self.packaging()
        r = self.run_script("--suite", "sid", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("release-2024-01-15", r.stderr)
        self.assertIn("hyphen before a digit", r.stderr)
        self.assertIn("--upstream-tag-match", r.stderr)

    def test_declared_set_a_never_gets_the_set_b_form(self):
        # Without --upstream-branch the version would be Set B's, from our own
        # tags: 0.0.post<N>, below everything published.
        self.git("tag", "2.93")
        self.packaging()
        (self.src / ".github").mkdir()
        decl = self.src / ".github/apt-packaging.toml"
        decl.write_text('kind = "A"\nupstream = "https://example.org/x"\n')
        r = self.run_script("--suite", "sid", check=False, set_a=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--upstream-branch", r.stderr)
        self.assertEqual(self.version("--suite", "sid"), "2.93-0+welland2")
        # A backport's form is still to come: as before. Undeclared, or any
        # other kind: Set B, as before.
        for text in ['kind = "A"\nvariant = "backport"\n', 'kind = "B"\n', ""]:
            with self.subTest(declaration=text):
                decl.write_text(text)
                self.assertEqual(self.run_script("--suite", "sid", set_a=False).stdout.strip(),
                                 "0.0.post4")

    def test_no_commits_of_ours(self):
        # Built on upstream itself: M is 0.
        self.git("tag", "v2.93")
        self.assertEqual(self.version("--suite", "sid"), "2.93-0+welland0")

    def test_origin_upstream_comes_first(self):
        # A CI checkout has origin/upstream and no local branch; with both,
        # the remote's is the one the build follows.
        self.git("tag", "v1.0", "HEAD~1")
        self.packaging()
        self.git("update-ref", "refs/remotes/origin/upstream", "upstream")
        self.git("branch", "-q", "-D", "upstream")
        up = self.sha7("origin/upstream")
        self.assertEqual(self.version("--suite", "sid"), f"1.0+git1.g{up}-0+welland2")
        self.git("branch", "-q", "upstream", "origin/upstream~1")     # a stale local branch
        self.assertEqual(self.version("--suite", "sid"), f"1.0+git1.g{up}-0+welland2")

    def test_tag_match(self):
        # The nearest tag is upstream's packaging tag; only v* are releases.
        self.git("tag", "v2.90", "HEAD~1")
        self.git("tag", "debian/2.90-1")
        up = self.sha7()
        self.packaging()
        r = self.run_script("--suite", "sid", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("debian/2.90-1", r.stderr)
        self.assertEqual(self.version("--suite", "sid", "--upstream-tag-match", "v[0-9]*"),
                         f"2.90+git1.g{up}-0+welland2")
        # A glob no tag matches is a mistake, not an upstream without tags.
        r = self.run_script("--suite", "sid", "--upstream-tag-match", "release-*", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no tag matching", r.stderr)

    def release_commits(self):
        """smartmontools' history: the releases are commits on the main line
        ("Release 7.5 RELEASE_7_5"), and the RELEASE_7_5 tag is on a commit
        off it, which git describe can't find from the main line."""
        self.commit("Release 7.4 RELEASE_7_4")
        self.commit("upstream: after 7.4")
        self.commit("Release 7.5 RELEASE_7_5")
        self.git("checkout", "-q", "-b", "svn-tags")
        self.commit("tag RELEASE_7_5")
        self.git("tag", "RELEASE_7_5")
        self.git("checkout", "-q", "upstream")
        self.git("branch", "-q", "-D", "svn-tags")

    def test_release_commit_subject(self):
        self.release_commits()
        self.commit("upstream: after 7.5")
        self.commit("Release notes: mention 7.5 RELEASE_7_5")   # not a release
        up = self.sha7()
        self.packaging()
        # Without the pattern the tag isn't found: upstream looks untagged.
        self.assertEqual(self.version("--suite", "sid"), f"0.0+git7.g{up}-0+welland2")
        for args, want in [
            (["--suite", "trixie"], f"7.5+git2.g{up}-0+welland2~deb13"),
            (["--suite", "sid"], f"7.5+git2.g{up}-0+welland2"),
            (["--suite", "forky", "--pr", "3"], f"7.5+git2.g{up}-0+welland2~deb14~pr3"),
        ]:
            with self.subTest(args=args):
                self.assertEqual(self.version(*args, "--upstream-release-subject", self.SUBJECT), want)
        # The group may hold the tag instead: it is normalised as a tag is.
        self.assertEqual(self.version("--suite", "sid", "--upstream-release-subject",
                                      r"^Release [0-9.]+ (RELEASE_[0-9_]+)$"),
                         f"7.5+git2.g{up}-0+welland2")

    def test_release_commit_subject_at_the_release(self):
        self.release_commits()
        self.packaging()
        self.assertEqual(self.version("--suite", "trixie", "--upstream-release-subject", self.SUBJECT),
                         "7.5-0+welland2~deb13")
        # Without a group, the release is everything the pattern matched.
        self.assertEqual(self.version("--suite", "trixie", "--upstream-release-subject",
                                      r"RELEASE_[0-9_]+$"), "7.5-0+welland2~deb13")

    def test_release_subject_from_the_declaration(self):
        self.release_commits()
        self.commit("upstream: after 7.5")
        up = self.sha7()
        self.packaging()
        (self.src / ".github").mkdir()
        decl = self.src / ".github/apt-packaging.toml"
        decl.write_text('kind = "A"\nupstream = "https://example.org/x"\n\n'
                        f"[version]\nrelease-subject = '{self.SUBJECT}'\n")
        self.assertEqual(self.version("--suite", "sid"), f"7.5+git1.g{up}-0+welland2")
        # The option wins over the declaration.
        self.assertEqual(self.version("--suite", "sid", "--upstream-release-subject",
                                      r"^Release (7\.4) "), f"7.4+git3.g{up}-0+welland2")
        for bad in ['[version]\nrelease-subject = "Release ("\n', "[version]\nrelease-subject = 7\n",
                    '[version]\nrelease-subject = ""\n', 'version = "7.5"\n',
                    "[version]\nrelease-subject = '(Release) (.+)'\n", "[version\n",
                    f"[version]\nrelease-subject = '{self.SUBJECT}'\ntags = \"v*\"\n"]:
            with self.subTest(declaration=bad):
                decl.write_text('kind = "A"\n' + bad)
                r = self.run_script("--suite", "sid", check=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("apt-packaging.toml", r.stderr)

    def test_declaration_is_checked_with_the_option_too(self):
        # An unknown [version] key fails here as it fails PKG-DECLARED, even
        # when the option gives the pattern.
        self.release_commits()
        self.packaging()
        (self.src / ".github").mkdir()
        (self.src / ".github/apt-packaging.toml").write_text('kind = "A"\n[version]\ntags = "v*"\n')
        r = self.run_script("--suite", "sid", "--upstream-release-subject", self.SUBJECT, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[version] has unknown keys tags", r.stderr)

    def test_release_is_the_nearest_on_upstreams_own_line(self):
        # A maintenance release made after 7.5 and merged into the main line:
        # the newest release commit by date, but not what the branch follows.
        # And smartmontools' twin: the same release commit again on a side
        # line (its svn tag), merged or not, is never the one counted from.
        self.commit("Release 7.4 RELEASE_7_4")
        self.git("checkout", "-q", "-b", "maint")
        self.git("checkout", "-q", "upstream")
        self.commit("upstream: towards 7.5")
        self.commit("Release 7.5 RELEASE_7_5")
        self.commit("upstream: after 7.5")
        later = {**self.env, "GIT_COMMITTER_DATE": "2026-09-25T12:00:00+0000",
                 "GIT_AUTHOR_DATE": "2026-09-25T12:00:00+0000"}
        self.git("checkout", "-q", "maint")
        for subject in ["maint: a fix", "Release 7.4.1 RELEASE_7_4_1"]:
            subprocess.run(["git", "-C", str(self.src), "commit", "-q", "--allow-empty", "-m", subject],
                           env=later, check=True, capture_output=True)
        self.git("checkout", "-q", "upstream")
        self.git("merge", "-q", "--no-ff", "-m", "Merge the 7.4 maintenance branch", "maint")
        self.commit("upstream: after the merge")
        up = self.sha7()
        # By date the newest release commit is 7.4.1's.
        self.assertEqual(self.git("log", "-1", "--format=%s", "--grep=^Release ", "upstream"),
                         "Release 7.4.1 RELEASE_7_4_1")
        self.packaging()
        # Since 7.5: one after, two on maint, the merge, one more.
        self.assertEqual(self.version("--suite", "sid", "--upstream-release-subject", self.SUBJECT),
                         f"7.5+git5.g{up}-0+welland2")

    def test_release_pattern_matching_without_its_group_fails(self):
        self.release_commits()
        self.packaging()
        r = self.run_script("--suite", "sid", "--upstream-release-subject", "^(nope)?Release", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("without its group", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_no_release_commit_fails(self):
        # A pattern nothing matches is never read as "no releases": the
        # version would silently drop to 0.0 and sort below what's published.
        self.packaging()
        r = self.run_script("--suite", "sid", "--upstream-release-subject", self.SUBJECT, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("release pattern", r.stderr)
        r = self.run_script("--suite", "sid", "--upstream-release-subject", "Release (", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--upstream-release-subject", r.stderr)
        # The subject gives something that isn't a version.
        self.git("checkout", "-q", "upstream")
        self.commit("Release candidate")
        self.git("checkout", "-q", "packaging")
        self.git("merge", "-q", "--no-ff", "-m", "Merge upstream", "upstream")
        r = self.run_script("--suite", "sid", "--upstream-release-subject", "^Release (.+)$", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a Debian upstream version", r.stderr)

    def test_debian_version(self):
        # debian/ came from Debian, for the release upstream is at: Debian's
        # version, revision and all, then ours.
        self.git("tag", "netplan-1.1.2")
        debian = PLACEHOLDER.replace("(0.0)", "(1.1.2-7)")
        self.packaging(changelog=debian)
        self.assertEqual(self.version("--suite", "trixie"), "1.1.2-7+welland2~deb13")
        self.assertEqual(self.version("--suite", "sid", "--pr", "9"), "1.1.2-7+welland2~pr9")
        # Upstream merged past the release: Debian's version no longer says
        # what is built.
        self.upstream_moves("upstream: three")
        self.assertEqual(self.version("--suite", "sid"), f"1.1.2+git1.g{self.sha7()}-0+welland3")

    def test_debian_version_only_for_this_release(self):
        self.git("tag", "v2.93")
        for top, want in [
            ("2.93-4", "2.93-4+welland2"),
            ("2.93+dfsg-1", "2.93+dfsg-1+welland2"),          # Debian's repack of it
            ("2.93-4+deb13u1", "2.93-4+deb13u1+welland2"),    # a stable update's debian/
            ("2.92-4", "2.93-0+welland2"),                    # an older release's debian/
            ("2.93~rc1-1", "2.93-0+welland2"),                # a release candidate's
            ("2.930-1", "2.93-0+welland2"),
            ("2.93", "2.93-0+welland2"),                      # native: no Debian revision
            ("2.93-0+welland7", "2.93-0+welland2"),           # ours, committed
            # ours on Debian's, committed: Debian's revision stays the base
            ("2.93-4+welland7~deb13", "2.93-4+welland2"),
            ("2.93-4+deb13u1+welland7", "2.93-4+deb13u1+welland2"),
            ("0.0", "2.93-0+welland2"),
        ]:
            with self.subTest(top=top):
                self.git("checkout", "-q", "upstream")
                self.git("branch", "-q", "-D", "packaging") if top != "2.93-4" else None
                self.packaging(changelog=PLACEHOLDER.replace("(0.0)", f"({top})"))
                self.assertEqual(self.version("--suite", "sid"), want)
                (self.src / "debian/changelog").unlink()
                (self.src / "debian/control").unlink()
                (self.src / "debian").rmdir()

    def test_unsupported_debian_repack_fails(self):
        # At the release and after it: Debian's version would sort above ours.
        self.git("tag", "v2.0", "HEAD~1")
        self.packaging(changelog=PLACEHOLDER.replace("(0.0)", "(2.0+repack-1)"))
        r = self.run_script("--suite", "sid", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only Debian's +dfsg and +ds", r.stderr)
        self.git("tag", "-d", "v2.0")
        self.git("tag", "v2.0", "upstream")
        r = self.run_script("--suite", "sid", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("2.0+repack-1", r.stderr)

    def test_no_committed_changelog(self):
        self.git("tag", "v2.93")
        self.packaging(changelog=None)
        self.assertEqual(self.version("--suite", "sid"), "2.93-0+welland2")

    def test_debian_epoch(self):
        # Debian's version has an epoch: without it ours would sort below
        # Debian's, so the build must be given the same one.
        self.git("tag", "v2.3")
        self.packaging(changelog=PLACEHOLDER.replace("(0.0)", "(1:2.3-1)"))
        self.assertEqual(self.version("--suite", "trixie", "--epoch", "1"), "1:2.3-1+welland2~deb13")
        for epoch in [[], ["--epoch", "2"]]:
            with self.subTest(epoch=epoch):
                r = self.run_script("--suite", "trixie", *epoch, check=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("pass --epoch 1", r.stderr)

    def test_debian_epoch_whatever_the_base(self):
        # The same when Debian's version isn't the base: an upstream without
        # tags, another release's debian/, upstream past the release, and an
        # entry of ours on top.
        def refused_without_then(want):
            for epoch in [[], ["--epoch", "2"]]:
                r = self.run_script("--suite", "sid", *epoch, check=False)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("pass --epoch 1", r.stderr)
            self.assertEqual(self.version("--suite", "sid", "--epoch", "1"), want)

        self.packaging(changelog=PLACEHOLDER.replace("(0.0)", "(1:2.2-1)"))
        up = self.sha7()
        refused_without_then(f"1:0.0+git2.g{up}-0+welland2")
        self.git("tag", "v2.3", "upstream")
        refused_without_then("1:2.3-0+welland2")
        self.upstream_moves("upstream: three")
        refused_without_then(f"1:2.3+git1.g{self.sha7()}-0+welland3")
        (self.src / "debian/changelog").write_text(PLACEHOLDER.replace("(0.0)", "(1:2.3-1+welland3~deb13)"))
        self.commit("ours on top")
        refused_without_then(f"1:2.3+git1.g{self.sha7()}-0+welland4")

    def test_epoch(self):
        # scanbd's declared exception: the epoch goes in front, as elsewhere.
        self.git("tag", "v1.5.1")
        self.packaging()
        self.assertEqual(self.version("--suite", "trixie", "--epoch", "1"), "1:1.5.1-0+welland2~deb13")

    def test_changelog_goes_on_top_of_debians(self):
        # Set A keeps its committed debian/changelog: Debian's entries stay
        # under the build's (docs/packaging.md, "The changelog").
        self.git("tag", "v1.0", "HEAD~1")
        up = self.sha7()
        debian = PLACEHOLDER.replace("(0.0)", "(1.0-2)")
        self.packaging(changelog=debian)
        sha = self.git("rev-parse", "HEAD")
        r = self.run_script("--suite", "trixie", "--pr", "5", "--write-changelog")
        want = f"1.0+git1.g{up}-0+welland2~deb13~pr5"
        self.assertEqual(r.stdout.strip(), want)
        text = (
            f"selftest-src ({want}) trixie; urgency=medium\n\n"
            f"  * Built from example/selftest-src@{sha}\n\n"
            " -- Self Test <selftest@invalid>  Thu, 24 Sep 2026 12:00:00 +0000\n\n"
            + debian)
        self.assertEqual((self.src / "debian/changelog").read_text(), text)
        # Again: the same version (the committed changelog is what's read),
        # and still one entry of ours.
        self.run_script("--suite", "trixie", "--pr", "5", "--write-changelog")
        self.assertEqual((self.src / "debian/changelog").read_text(), text)

    def test_needs_the_owner_tag_and_the_branch(self):
        self.git("tag", "v1.0")
        self.packaging()
        for args in [["--upstream-branch", "upstream"], ["--owner-tag", "welland"],
                     ["--upstream-branch", "upstream", "--owner-tag", "Welland"],
                     ["--upstream-branch", "upstream", "--owner-tag", "welland",
                      "--upstream-version", "1.0"],
                     ["--upstream-release-subject", "x"],
                     ["--upstream-tag-match", "v*"]]:
            with self.subTest(args=args):
                r = self.run_script("--suite", "sid", *args, check=False, set_a=False)
                self.assertNotEqual(r.returncode, 0)

    def test_missing_upstream_branch_fails(self):
        self.git("tag", "v1.0")
        self.packaging()
        r = self.run_script("--suite", "sid", "--owner-tag", "welland", "--upstream-branch", "master",
                            check=False, set_a=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("neither origin/master nor master exists", r.stderr)

    def test_unrelated_upstream_fails(self):
        # A flat snapshot of upstream's files, without its history.
        self.git("checkout", "-q", "--orphan", "packaging")
        self.commit("a snapshot")
        r = self.run_script("--suite", "sid", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no history in common", r.stderr)

    def test_shallow_clone_is_refused(self):
        self.git("tag", "v1.0")
        self.packaging()
        clone = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, clone)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--no-single-branch",
                        f"file://{self.src}", str(clone / "c")],
                       check=True, env=self.env, capture_output=True)
        r = subprocess.run(["python3", str(SCRIPT), "--source-dir", str(clone / "c"), "--suite", "sid",
                            "--owner-tag", "welland", "--upstream-branch", "upstream"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("shallow", r.stderr)


if __name__ == "__main__":
    unittest.main()
