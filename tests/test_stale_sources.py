"""build-deb/raspbian/stale-sources.py, offline: apt's real error messages
from Raspbian builds (tests/fixtures/apt-errors/), with apt and dpkg replaced
by fixed answers."""
import importlib.util
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("stale_sources", HERE.parent / "build-deb/raspbian/stale-sources.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def fixture(name: str) -> list[str]:
    return (HERE / "fixtures/apt-errors" / name).read_text().splitlines()


# What apt would answer in raspbian forky with forky-staging, 2026-09-29.
CANDIDATES = {
    "librust-schemars-derive-dev": {"Version": "1.2.2-1", "Source": "rust-schemars-derive"},
    "librust-tiff-dev": {"Version": "0.11.3-2", "Source": "rust-tiff"},
    "librust-pyo3-dev": {"Version": "0.28.2-1", "Source": "rust-pyo3"},
    "librust-schemars-dev": {"Version": "1.2.2-2", "Source": "rust-schemars"},
    # A binNMU: built from 0.8.22-3, which is the newest source.
    "librust-schemars-0.8-dev": {"Version": "0.8.22-3+b27", "Source": "rust-schemars-0.8"},
    "librust-serde-derive-internals-dev": {"Version": "0.30.0-4", "Source": "rust-serde-derive-internals"},
}
SOURCES = {
    "rust-schemars-derive": "1.2.2-2",
    "rust-tiff": "0.11.3-3",
    "rust-pyo3": "0.28.2-1",
    "rust-schemars": "1.2.2-2",
    "rust-schemars-0.8": "0.8.22-3",
    "rust-serde-derive-internals": "0.30.0-4",
}


def candidate(pkg: str) -> dict[str, str]:
    return dict(CANDIDATES.get(pkg, {}))


def newest_source(src: str) -> str | None:
    return SOURCES.get(src)


@unittest.skipUnless(shutil.which("dpkg"), "needs dpkg --compare-versions")
class Stale(unittest.TestCase):
    def stale(self, lines: list[str]) -> list[str]:
        return [s[0] for s in ss.stale_sources(ss.names_from(lines), candidate, newest_source, ss.dpkg_newer)]

    def test_breaks_chain(self):
        # librust-serde-derive-internals-dev Breaks the old schemars-derive.
        self.assertEqual(self.stale(fixture("schemars-derive-breaks.txt")), ["rust-schemars-derive"])

    def test_missing_version(self):
        # A build dependency on a version only the newer source has.
        self.assertEqual(self.stale(fixture("schemars-derive-missing-version.txt")), ["rust-schemars-derive"])

    def test_old_binary_needs_vanished_dependency(self):
        self.assertEqual(self.stale(fixture("tiff-stale-weezl.txt")), ["rust-tiff"])

    def test_binnmu_is_not_stale(self):
        self.assertEqual(self.stale(["librust-schemars-0.8-dev:armhf=0.8.22-3+b27 Depends foo"]), [])

    def test_unknown_names_and_words_dropped(self):
        lines = ["E: Unable to satisfy dependencies. Reached two conflicting assignments:",
                 "   2. builddeps:./:armhf Depends no-such-package (>= 1) but it is not going to be installed",
                 "      but none of the choices are installable:"]
        self.assertEqual(self.stale(lines), [])

    def test_each_source_once(self):
        line = "librust-schemars-derive-dev:armhf=1.2.2-1 Depends librust-schemars-derive-dev"
        self.assertEqual(self.stale([line, line]), ["rust-schemars-derive"])


class Names(unittest.TestCase):
    def test_english_words_are_not_names(self):
        names = ss.names_from(fixture("tiff-stale-weezl.txt"))
        for w in ("to", "satisfy", "selected", "install", "because", "none", "choices", "builddeps"):
            self.assertNotIn(w, names)
        self.assertIn("librust-tiff-dev", names)
        self.assertIn("librust-weezl-0.1+default-dev", names)

    def test_only_problem_lines(self):
        self.assertEqual(ss.names_from(["Reading package lists... Done", "Get:1 foo bar"]), [])

    def test_version_numbers_are_not_names(self):
        self.assertNotIn("0.11.3-2", ss.names_from(fixture("tiff-stale-weezl.txt")))


class Script(unittest.TestCase):
    def test_no_input_no_output(self):
        r = subprocess.run(["python3", str(HERE.parent / "build-deb/raspbian/stale-sources.py")],
                           input="", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "\n"[:0]))


if __name__ == "__main__":
    unittest.main()
