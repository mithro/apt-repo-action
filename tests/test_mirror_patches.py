"""Tests for scripts/mirror-patches.py (docs/packaging.md, "Our own patches on
a mirror"), against a local git repository: a mirrored `master`, and our
patch branches on it.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/mirror-patches.py"
_loader = importlib.machinery.SourceFileLoader("mirror_patches", str(SCRIPT))
mp = importlib.util.module_from_spec(importlib.util.spec_from_loader("mirror_patches", _loader))
_loader.exec_module(mp)

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


class MirrorPatches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "src"
        git(self.tmp.name, "init", "-q", "-b", "master", str(self.src))
        self.write("main.c", "int main(void) { return 0; }\n", "upstream")
        # Two topics, each on master: one of two commits, one of one.
        git(self.src, "checkout", "-q", "-b", "patches/axfr")
        self.write("axfr.c", "stream\n", "axfr: stream it")
        self.write("axfr.c", "stream\nfaster\n", "axfr: faster")
        git(self.src, "checkout", "-q", "-b", "patches/dump-config", "master")
        self.write("dump.c", "dump\n", "--dump-config")
        git(self.src, "checkout", "-q", "master")
        self.pins = [{"branch": b, "commit": git(self.src, "rev-parse", b), "topic": b.removeprefix("patches/")}
                     for b in ("patches/axfr", "patches/dump-config")]
        os.environ.update({k: v for k, v in ENV.items() if k.startswith("GIT_")})

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text, msg):
        (self.src / name).write_text(text)
        git(self.src, "add", name)
        git(self.src, "commit", "-q", "-m", msg)

    def test_generate(self):
        out = Path(self.tmp.name) / "patches"
        series = mp.generate(self.src, self.pins, out)
        self.assertEqual(series, ["axfr/0001-axfr-stream-it.patch", "axfr/0002-axfr-faster.patch",
                                  "dump-config/0001-dump-config.patch"])
        self.assertEqual((out / "series").read_text().split(), series)
        # One patch per commit, against master; reproducible (no commit ids).
        self.assertIn("+faster", (out / series[1]).read_text())
        self.assertTrue((out / series[0]).read_text().startswith("From 0000000000000000000000000000000000000000"))

    def test_relative_paths_as_the_workflow_runs_it(self):
        # deb.yml runs it from the workspace: --source src, --out defaulting
        # to src/debian/patches, both relative.
        (self.src / "debian/source").mkdir(parents=True)
        (self.src / "debian/source/format").write_text("3.0 (quilt)\n")
        d = Path(self.tmp.name) / "decl.toml"
        d.write_text(f'[[mirror.patches]]\nbranch = "patches/axfr"\ncommit = "{self.pins[0]["commit"]}"\n')
        r = subprocess.run(["python3", str(SCRIPT), "--declaration", str(d), "--source", "src"],
                           cwd=self.tmp.name, capture_output=True, text=True, env=ENV)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        patches = self.src / "debian/patches"
        self.assertEqual((patches / "series").read_text().split(),
                         ["axfr/0001-axfr-stream-it.patch", "axfr/0002-axfr-faster.patch"])
        for f in (patches / "series").read_text().split():
            self.assertTrue((patches / f).is_file(), f)
        self.assertTrue((patches / mp.MARKER).is_file())

    def test_upstream_moved_patches_still_apply(self):
        self.write("NEWS", "2.93\n", "upstream moves on")
        self.assertIsNone(mp.check(self.src, self.pins))
        out = Path(self.tmp.name) / "patches"
        # Generated against each branch's own base, applied to the new tip.
        self.assertEqual(len(mp.generate(self.src, self.pins, out)), 3)

    def test_upstream_conflicts(self):
        self.write("axfr.c", "upstream's own\n", "upstream adds axfr.c")
        self.assertEqual(mp.check(self.src, self.pins), ("axfr", "axfr/0001-axfr-stream-it.patch"))
        # The scratch worktree is gone, and master untouched.
        self.assertEqual(git(self.src, "worktree", "list").count("\n"), 0)
        self.assertEqual((self.src / "axfr.c").read_text(), "upstream's own\n")

    def test_generate_fails_when_a_patch_doesnt_apply(self):
        # dpkg-source --before-build dry-runs only the first unapplied patch
        # and, when that fails, takes the series as applied: exit 0, nothing
        # patched. So generating must fail itself, as the build would apply.
        self.write("axfr.c", "upstream's own\n", "upstream adds axfr.c")
        with self.assertRaises(mp.Conflict) as cm:
            mp.generate(self.src, self.pins, Path(self.tmp.name) / "patches")
        self.assertEqual((cm.exception.topic, cm.exception.patch), ("axfr", "axfr/0001-axfr-stream-it.patch"))

    def test_a_failed_generate_leaves_nothing(self):
        # Nothing at --out unless every patch applies: a caller that carries
        # on past the failure (continue-on-error) mustn't find a half-written
        # series without the .mirror-patches marker, which build-deb would
        # then build without checking.
        self.write("axfr.c", "upstream's own\n", "upstream adds axfr.c")
        out = Path(self.tmp.name) / "patches"
        with self.assertRaises(mp.Conflict):
            mp.generate(self.src, self.pins, out)
        self.assertFalse(out.exists())
        self.assertEqual([x.name for x in Path(self.tmp.name).iterdir()], ["src"])
        # And a refused one (a binary change) the same.
        with self.assertRaisesRegex(mp.Error, "binary"):
            mp.generate(self.src, self.binary_topic(False), out)
        self.assertFalse(out.exists())

    def test_check_and_generate_agree(self):
        for i, conflicting in enumerate((False, True)):
            with self.subTest(conflicting=conflicting):
                if i:  # a fresh fixture; the last one cleaned up
                    self.tearDown()
                    self.setUp()
                if conflicting:
                    self.write("dump.c", "upstream's dump\n", "upstream adds dump.c")
                got = mp.check(self.src, self.pins)
                try:
                    mp.generate(self.src, self.pins, Path(self.tmp.name) / "patches")
                    generated = None
                except mp.Conflict as c:
                    generated = (c.topic, c.patch)
                self.assertEqual(got, generated)
                self.assertEqual(got is not None, conflicting)

    def binary_topic(self, with_text):
        git(self.src, "checkout", "-q", "-b", "patches/logo", "master")
        (self.src / "logo.bin").write_bytes(bytes(range(256)))
        if with_text:
            (self.src / "main.c").write_text("int main(void) { return 1; }\n")
            git(self.src, "add", "main.c")
        git(self.src, "add", "logo.bin")
        git(self.src, "commit", "-q", "-m", "a logo")
        git(self.src, "checkout", "-q", "master")
        return [{"branch": "patches/logo", "commit": git(self.src, "rev-parse", "patches/logo"), "topic": "logo"}]

    def test_binary_changes_refused(self):
        for i, with_text in enumerate((False, True)):  # a binary hunk beside a text one is refused too
            with self.subTest(with_text=with_text):
                if i:  # a fresh fixture; the last one cleaned up
                    self.tearDown()
                    self.setUp()
                with self.assertRaisesRegex(mp.Error, "changes a binary file"):
                    mp.generate(self.src, self.binary_topic(with_text), Path(self.tmp.name) / "patches")

    def test_stacked_patch_branches(self):
        # patches/faster is built on patches/axfr: its patches start at axfr's pin.
        git(self.src, "checkout", "-q", "-b", "patches/faster", "patches/axfr")
        self.write("axfr.c", "stream\nfaster\nfastest\n", "axfr: fastest")
        git(self.src, "checkout", "-q", "master")
        pins = self.pins[:1] + [{"branch": "patches/faster", "commit": git(self.src, "rev-parse", "patches/faster"),
                                 "topic": "faster"}]
        series = mp.generate(self.src, pins, Path(self.tmp.name) / "patches")
        self.assertEqual(series, ["axfr/0001-axfr-stream-it.patch", "axfr/0002-axfr-faster.patch",
                                  "faster/0001-axfr-fastest.patch"])

    def test_empty_files_refused(self):
        # A patch that creates (or deletes) an empty file has no hunk: patch
        # creates nothing, and the check would pass with the file missing.
        git(self.src, "checkout", "-q", "-b", "patches/empty", "master")
        (self.src / "EMPTY").write_text("")
        git(self.src, "add", "EMPTY")
        git(self.src, "commit", "-q", "-m", "an empty file")
        git(self.src, "checkout", "-q", "master")
        pins = [{"branch": "patches/empty", "commit": git(self.src, "rev-parse", "patches/empty"), "topic": "empty"}]
        with self.assertRaisesRegex(mp.Error, "empty file"):
            mp.generate(self.src, pins, Path(self.tmp.name) / "patches")

    def test_checks_the_tree_the_build_sees(self):
        # git archive substitutes $Format:...$ in an export-subst file (and
        # drops export-ignore ones); the build's checkout doesn't. A patch to
        # such a file must be checked against the checkout.
        (self.src / ".gitattributes").write_text("version.txt export-subst\nbuild-only.txt export-ignore\n")
        (self.src / "version.txt").write_text("version $Format:%H$\n")
        (self.src / "build-only.txt").write_text("kept by the checkout\n")
        git(self.src, "add", ".gitattributes", "version.txt", "build-only.txt")
        git(self.src, "commit", "-q", "-m", "attributes")
        git(self.src, "checkout", "-q", "-b", "patches/version", "master")
        (self.src / "version.txt").write_text("version $Format:%H$\npatched\n")
        (self.src / "build-only.txt").write_text("kept by the checkout\npatched\n")
        git(self.src, "commit", "-q", "-am", "patch both")
        git(self.src, "checkout", "-q", "master")
        pins = [{"branch": "patches/version", "commit": git(self.src, "rev-parse", "patches/version"), "topic": "version"}]
        self.assertEqual(mp.generate(self.src, pins, Path(self.tmp.name) / "patches"),
                         ["version/0001-patch-both.patch"])

    def test_pinned_commit_not_the_branch_tip(self):
        # The build uses the pin, not whatever the branch has moved to since.
        git(self.src, "checkout", "-q", "patches/dump-config")
        self.write("dump.c", "dump\nmore\n", "unpinned work")
        git(self.src, "checkout", "-q", "master")
        out = Path(self.tmp.name) / "patches"
        mp.generate(self.src, self.pins, out)
        self.assertNotIn("more", (out / "dump-config/0001-dump-config.patch").read_text())
        self.assertFalse((out / "dump-config/0002-unpinned-work.patch").exists())

    def test_upstream_took_the_patch(self):
        git(self.src, "merge", "-q", "--ff-only", "patches/dump-config")
        with self.assertRaisesRegex(mp.Error, "upstream took it"):
            mp.generate(self.src, self.pins, Path(self.tmp.name) / "patches")

    def test_missing_commit(self):
        pins = [{**self.pins[0], "commit": "1" * 40}]
        with self.assertRaisesRegex(mp.Error, "fetch-depth: 0"):
            mp.generate(self.src, pins, Path(self.tmp.name) / "patches")

    def test_declaration(self):
        d = Path(self.tmp.name) / "decl.toml"
        c = self.pins[0]["commit"]
        d.write_text(f'kind = "mirror"\n[[mirror.patches]]\nbranch = "patches/axfr"\ncommit = "{c}"\n')
        self.assertEqual(mp.load(d), [{"branch": "patches/axfr", "commit": c, "topic": "axfr"}])
        for body, err in [(f'branch = "axfr"\ncommit = "{c}"', "patches/<topic>"),
                          ('branch = "patches/axfr"\ncommit = "abc"', "40-hex"),
                          (f'branch = "patches/axfr"\ncommit = "{c}"\nbase = "x"', "unknown keys")]:
            d.write_text(f"[[mirror.patches]]\n{body}\n")
            with self.subTest(err=err), self.assertRaisesRegex(mp.Error, err):
                mp.load(d)
        d.write_text('kind = "mirror"\n')
        self.assertEqual(mp.load(d), [])

    def test_committed_patches_refused(self):
        (self.src / "debian/source").mkdir(parents=True)
        (self.src / "debian/source/format").write_text("3.0 (quilt)\n")
        (self.src / "debian/patches").mkdir()
        (self.src / "debian/patches/series").write_text("hand.patch\n")
        d = Path(self.tmp.name) / "decl.toml"
        d.write_text(f'[[mirror.patches]]\nbranch = "patches/axfr"\ncommit = "{self.pins[0]["commit"]}"\n')
        r = subprocess.run(["python3", str(SCRIPT), "--declaration", str(d), "--source", str(self.src)],
                           capture_output=True, text=True, env=ENV)
        self.assertEqual(r.returncode, 1)
        self.assertIn("must not commit debian/patches", r.stdout)


if __name__ == "__main__":
    unittest.main()
