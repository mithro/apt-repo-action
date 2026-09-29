"""Tests for scripts/collect-debs.py: which artifacts a publish downloads, and
the suite each one's .debs go to (README.md, artifact names). No network.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
"""
import importlib.machinery
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/collect-debs.py"
_loader = importlib.machinery.SourceFileLoader("collect_debs", str(SCRIPT))
cd = importlib.util.module_from_spec(importlib.util.spec_from_loader("collect_debs", _loader))
_loader.exec_module(cd)

SUITES = ["bookworm", "trixie", "raspbian-trixie", "sid"]


def art(name, id, expired=False):
    return {"name": name, "id": id, "expired": expired}


class Select(unittest.TestCase):
    def test_pattern_matches_whole_name(self):
        got = cd.select([art("debs-trixie-amd64", 1), art("dbgsym-trixie-amd64", 2),
                         art("xdebs-trixie", 3)], "debs-*")
        self.assertEqual([a["name"] for a in got], ["debs-trixie-amd64"])

    def test_newest_of_each_name(self):
        # A re-run uploads a new artifact under a name the first attempt used.
        got = cd.select([art("debs-sid-amd64", 7), art("debs-sid-amd64", 12),
                         art("debs-sid-arm64", 9)], "debs-*")
        self.assertEqual([(a["name"], a["id"]) for a in got],
                         [("debs-sid-amd64", 12), ("debs-sid-arm64", 9)])

    def test_expired_skipped(self):
        got = cd.select([art("debs-sid-amd64", 12, expired=True), art("debs-sid-amd64", 7)], "debs-*")
        self.assertEqual([a["id"] for a in got], [7])


class SuiteOf(unittest.TestCase):
    def test_names(self):
        cases = {
            "debs-trixie": "trixie",
            "debs-trixie-amd64": "trixie",
            "debs-raspbian-trixie-armhf": "raspbian-trixie",
            "debs-bookworm-armhf-openocd-stable": "bookworm",
            "debs-forky-amd64": None,       # not a configured suite
            "debs-trixiex-amd64": None,     # a suite followed by something other than a dash
            "trixie-amd64": None,           # no debs- prefix
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(cd.suite_of(name, SUITES), want)


class Regroup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.staging = self.tmp / "staging"
        self.dest = self.tmp / "apt-repo"

    def deb(self, rel):
        p = self.staging / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"deb")

    def test_regroup(self):
        self.deb("debs-trixie-amd64/a_1_amd64.deb")
        self.deb("debs-raspbian-trixie-armhf/sub/b_1_armhf.deb")
        self.deb("debs-forky-amd64/c_1_amd64.deb")      # names no configured suite
        placed = cd.regroup(self.staging, SUITES, self.dest)
        self.assertEqual(placed["trixie"], ["a_1_amd64.deb"])
        self.assertEqual(placed["raspbian-trixie"], ["b_1_armhf.deb"])
        self.assertTrue((self.dest / "raspbian-trixie/b_1_armhf.deb").is_file())
        # Every suite directory exists, even an empty one.
        self.assertTrue((self.dest / "sid").is_dir())

    def test_debs_outside_an_artifact_directory_refused(self):
        # What download-artifact v5+ does with one artifact and no path of its own.
        self.deb("a_1_amd64.deb")
        with self.assertRaises(cd.Error):
            cd.regroup(self.staging, SUITES, self.dest)

    def test_no_staging(self):
        placed = cd.regroup(self.staging, SUITES, self.dest)
        self.assertEqual(placed, {s: [] for s in SUITES})


if __name__ == "__main__":
    unittest.main()
