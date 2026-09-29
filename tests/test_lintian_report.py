"""Tests for scripts/lintian-report.py: lintian's output turned into GitHub
annotations and a step summary (docs/packaging.md, "Package contents").

Run: python3 -m unittest discover -s tests -p 'test_*.py'
"""
import importlib.machinery
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/lintian-report.py"
_loader = importlib.machinery.SourceFileLoader("lintian_report", str(SCRIPT))
lr = importlib.util.module_from_spec(importlib.util.spec_from_loader("lintian_report", _loader))
_loader.exec_module(lr)

# lintian 2.116 (bookworm) on the self-test fixture, plus a source tag and a
# message needing escaping.
OUTPUT = """\
E: apt-repo-selftest-src: bogus-mail-host Maintainer selftest@invalid
E: apt-repo-selftest-src: no-copyright-file
W: apt-repo-selftest-src: empty-binary-package
W: apt-repo-selftest-src: empty-binary-package
N: some note lintian prints
I: apt-repo-selftest-src: extended-description-is-probably-too-short
W: foo source: odd-tag 100% done, really
"""


def run(text: str | bytes, mode: str, status: str = "0") -> tuple[int, str, str, dict]:
    with tempfile.TemporaryDirectory() as d:
        log = Path(d, "lintian.txt")
        log.write_bytes(text if isinstance(text, bytes) else text.encode())
        summary, outputs = Path(d, "summary.md"), Path(d, "outputs")
        out = io.StringIO()
        rc = lr.main(["--mode", mode, "--status", status, "--summary", str(summary),
                      "--outputs", str(outputs), str(log)], out=out)
        got = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
        return rc, out.getvalue(), summary.read_text(), got


class Parse(unittest.TestCase):
    def test_tags(self):
        tags = lr.parse(OUTPUT)
        self.assertEqual([(t.level, t.package, t.tag) for t in tags], [
            ("E", "apt-repo-selftest-src", "bogus-mail-host"),
            ("E", "apt-repo-selftest-src", "no-copyright-file"),
            ("W", "apt-repo-selftest-src", "empty-binary-package"),
            ("W", "apt-repo-selftest-src", "empty-binary-package"),
            ("I", "apt-repo-selftest-src", "extended-description-is-probably-too-short"),
            ("W", "foo source", "odd-tag"),
        ])
        self.assertEqual(tags[0].detail, "Maintainer selftest@invalid")
        self.assertEqual(tags[1].detail, "")


class Report(unittest.TestCase):
    def test_warn_never_fails(self):
        rc, out, summary, got = run(OUTPUT, "warn")
        self.assertEqual(rc, 0)
        self.assertEqual(got, {"errors": "2", "warnings": "3"})
        # Errors are only warnings while lintian doesn't fail builds.
        self.assertNotIn("::error", out)
        self.assertIn("::warning title=lintian E%3A no-copyright-file::apt-repo-selftest-src: no-copyright-file",
                      out)
        self.assertIn("100%25 done", out)
        self.assertIn("| E | apt-repo-selftest-src | `no-copyright-file` | 1 |", summary)
        self.assertIn("| W | apt-repo-selftest-src | `empty-binary-package` | 2 |", summary)
        # Informational tags are in the log, not annotated.
        self.assertNotIn("title=lintian I", out)

    def test_error_fails_on_errors(self):
        rc, out, summary, _ = run(OUTPUT, "error")
        self.assertEqual(rc, 1)
        self.assertIn("::error title=lintian E%3A no-copyright-file::", out)
        self.assertIn("::warning title=lintian W%3A empty-binary-package::", out)

    def test_error_passes_clean(self):
        rc, out, summary, got = run("W: p: some-warning\n", "error")
        self.assertEqual(rc, 0)
        self.assertEqual(got, {"errors": "0", "warnings": "1"})

    def test_clean(self):
        rc, out, summary, got = run("", "warn")
        self.assertEqual(rc, 0)
        self.assertIn("no errors or warnings", summary)

    def test_lintian_itself_failed(self):
        # Fatal in both modes: warn only forgives lintian's findings.
        for mode in ("warn", "error"):
            rc, out, _, _ = run("", mode, status="2")
            self.assertEqual(rc, 1, mode)
            self.assertIn("::error title=lintian::lintian could not run: it failed (exit status 2)", out)

    def test_crash_is_status_2(self):
        # A missing log file: the script crashes, and says so with 2.
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, str(SCRIPT), "--mode", "warn", "--summary", "",
                                "--outputs", "", str(Path(d, "missing.txt"))],
                               capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("FileNotFoundError", p.stderr)

    def test_unexpected_input(self):
        # Not lintian's format, or not text: ignored, never a crash.
        rc, out, summary, got = run(b"garbage\nE:\nE: :\nW: p:\n\x00\xff\xfe\n", "warn")
        self.assertEqual((rc, got), (0, {"errors": "0", "warnings": "0"}))

    def test_bad_mode(self):
        with self.assertRaises(SystemExit):
            run("", "loud")


if __name__ == "__main__":
    unittest.main()
