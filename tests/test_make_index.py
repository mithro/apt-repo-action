"""Tests for scripts/make-index.py: the packages the landing page lists for a
suite. Needs dpkg. No network.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
"""
import importlib.machinery
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/make-index.py"
_loader = importlib.machinery.SourceFileLoader("make_index", str(SCRIPT))
mi = importlib.util.module_from_spec(importlib.util.spec_from_loader("make_index", _loader))
_loader.exec_module(mi)


def stanza(package, version, arch="amd64", **more):
    fields = {"Package": package, "Version": version, "Architecture": arch, **more}
    return "\n".join(f"{k}: {v}" for k, v in fields.items())


@unittest.skipUnless(shutil.which("dpkg"), "needs dpkg")
class PackagesIn(unittest.TestCase):
    def setUp(self):
        self.suite = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.suite)

    def listed(self, *stanzas):
        (self.suite / "Packages").write_text("\n\n".join(stanzas) + "\n")
        return mi.packages_in(self.suite)

    def test_newest_is_dpkg_order_not_string_order(self):
        # mithro/smartmontools' page listed welland9 while welland10 was published.
        for old, new in [("7.5+git583.g06489e0-0+welland9~deb13", "7.5+git583.g06489e0-0+welland10~deb13"),
                         ("0.0.post9~deb13", "0.0.post10~deb13"),
                         ("1.0~rc1-0+welland1", "1.0-0+welland1"),
                         ("1.2.0", "1.2.0-0+welland4~deb13")]:
            for order in [(old, new), (new, old)]:
                with self.subTest(old=old, new=new, first=order[0]):
                    got = self.listed(*(stanza("smartmontools", v) for v in order))
                    self.assertEqual(got, [("smartmontools", new, "amd64", "")])

    def test_one_row_per_package_and_architecture(self):
        got = self.listed(stanza("b", "1.0", "arm64"), stanza("b", "1.0"), stanza("a", "2.0", "all"),
                          stanza("dep", "3.0", "all", **{"Bundled-From": "someone/dep"}))
        self.assertEqual(got, [("a", "2.0", "all", ""), ("b", "1.0", "amd64", ""), ("b", "1.0", "arm64", ""),
                               ("dep", "3.0", "all", "someone/dep")])

    def test_no_packages_file(self):
        self.assertEqual(mi.packages_in(self.suite), [])


if __name__ == "__main__":
    unittest.main()
