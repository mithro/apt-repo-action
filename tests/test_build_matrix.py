"""Tests for scripts/build-matrix.py against docs/packaging.md ("Suites",
"Architectures", "The declaration").

Run: python3 -m unittest discover -s tests -p 'test_*.py'
"""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/build-matrix.py"
_loader = importlib.machinery.SourceFileLoader("build_matrix", str(SCRIPT))
bm = importlib.util.module_from_spec(importlib.util.spec_from_loader("build_matrix", _loader))
_loader.exec_module(bm)

ANY_AND_ALL = ["all", "any"]   # the hello fixture: a -common package and a command
ANY_ONLY = ["any"]
ALL_ONLY = ["all"]


def jobs(plan):
    return [(j["suite"], j["arch"], j["runner"], j["arch-all"]) for j in plan["build"]]


class Defaults(unittest.TestCase):
    def test_default_architecture_dependent(self):
        p = bm.plan({}, ANY_AND_ALL, "declared", "declared")
        # trixie forky sid, then the Raspbian suites, since armhf is built.
        self.assertEqual(p["suites"], ["trixie", "forky", "sid", "raspbian-trixie", "raspbian-forky"])
        self.assertEqual(p["architectures"], "amd64 i386 arm64 armhf riscv64 all")
        self.assertEqual(len(p["build"]), 3 * 5 + 2)
        self.assertIn(("trixie", "amd64", "ubuntu-24.04", "true"), jobs(p))
        self.assertIn(("trixie", "i386", "ubuntu-24.04", "false"), jobs(p))
        self.assertIn(("trixie", "arm64", "ubuntu-24.04-arm", "false"), jobs(p))
        self.assertIn(("trixie", "armhf", "ubuntu-24.04-arm", "false"), jobs(p))
        self.assertIn(("trixie", "riscv64", "ubuntu-24.04", "false"), jobs(p))
        # Raspbian has one architecture, so its job builds everything.
        self.assertIn(("raspbian-trixie", "armhf", "ubuntu-24.04-arm", "true"), jobs(p))

    def test_arch_all_built_once_per_suite(self):
        p = bm.plan({}, ANY_AND_ALL, "declared", "declared")
        for suite in p["suites"]:
            n = sum(1 for j in p["build"] if j["suite"] == suite and j["arch-all"] == "true")
            self.assertEqual(n, 1, suite)

    def test_all_bundling_into_raspbian(self):
        # paramiko-insecure: pure Python, bundling an arch-dependent
        # dependency whose ARMv6 build is in its raspbian suites.
        decl = {"architectures": "all",
                "suites": ["bookworm", "trixie", "raspbian-bookworm", "raspbian-trixie"],
                "depends": [{"repo": "o/dep", "bundle": True, "reason": "r"}]}
        p = bm.plan(decl, ALL_ONLY, "declared", "declared")
        self.assertEqual(p["suites"], ["bookworm", "trixie", "raspbian-bookworm", "raspbian-trixie"])
        # Built once per Debian suite, and the same files uploaded again for
        # the Raspbian suite of the codename: no Raspbian build job.
        self.assertEqual(jobs(p), [("bookworm", "all", "ubuntu-24.04", "true"),
                                   ("trixie", "all", "ubuntu-24.04", "true")])
        self.assertEqual([j.get("also") for j in p["build"]], ["raspbian-bookworm", "raspbian-trixie"])
        # The Raspbian suites are install-tested in the Raspbian root (armhf).
        self.assertIn({"suite": "raspbian-trixie", "arch": "armhf", "runner": "ubuntu-24.04-arm"},
                      p["install"])
        self.assertEqual(p["architectures"], "all")

    def test_no_arch_all_packages(self):
        p = bm.plan({}, ANY_ONLY, "declared", "declared")
        self.assertEqual(p["architectures"], "amd64 i386 arm64 armhf riscv64")

    def test_all_only(self):
        p = bm.plan({"architectures": "all"}, ALL_ONLY, "declared", "declared")
        # Architecture: all publishes only the Debian suites.
        self.assertEqual(p["suites"], ["trixie", "forky", "sid"])
        self.assertEqual(p["architectures"], "all")
        self.assertEqual(jobs(p), [(s, "all", "ubuntu-24.04", "true") for s in ["trixie", "forky", "sid"]])
        self.assertEqual([(i["suite"], i["arch"]) for i in p["install"]],
                         [("trixie", "all"), ("forky", "all"), ("sid", "all")])


class Declared(unittest.TestCase):
    def test_bookworm_listed_exactly(self):
        decl = {"architectures": "all", "suites": ["bookworm", "trixie", "forky", "sid"]}
        p = bm.plan(decl, ALL_ONLY, "declared", "declared")
        self.assertEqual(p["suites"], ["bookworm", "trixie", "forky", "sid"])

    def test_no_riscv64_in_bookworm(self):
        decl = {"suites": ["bookworm", "trixie"], "architectures": ["amd64", "riscv64"]}
        p = bm.plan(decl, ANY_ONLY, "declared", "declared")
        self.assertEqual(sorted((j["suite"], j["arch"]) for j in p["build"]),
                         [("bookworm", "amd64"), ("trixie", "amd64"), ("trixie", "riscv64")])

    def test_hardware_specific(self):
        # fpgas.online-fpga-tools: Raspberry Pi 5, arm64 and armhf, bookworm added.
        decl = {"architectures": ["arm64", "armhf"],
                "suites": ["bookworm", "trixie", "sid", "raspbian-bookworm", "raspbian-trixie"]}
        p = bm.plan(decl, ANY_AND_ALL, "declared", "declared")
        self.assertEqual(p["architectures"], "arm64 armhf all")
        # No amd64: arm64 builds the Architecture: all packages.
        self.assertIn(("trixie", "arm64", "ubuntu-24.04-arm", "true"), jobs(p))
        self.assertIn(("trixie", "armhf", "ubuntu-24.04-arm", "false"), jobs(p))
        self.assertIn(("raspbian-bookworm", "armhf", "ubuntu-24.04-arm", "true"), jobs(p))
        self.assertEqual([(i["suite"], i["arch"], i["runner"]) for i in p["install"]],
                         [("bookworm", "arm64", "ubuntu-24.04-arm"), ("trixie", "arm64", "ubuntu-24.04-arm"),
                          ("sid", "arm64", "ubuntu-24.04-arm"), ("raspbian-bookworm", "armhf", "ubuntu-24.04-arm"),
                          ("raspbian-trixie", "armhf", "ubuntu-24.04-arm")])

    def test_default_suites_without_armhf_have_no_raspbian(self):
        p = bm.plan({"architectures": ["arm64"]}, ANY_ONLY, "declared", "declared")
        self.assertEqual(p["suites"], ["trixie", "forky", "sid"])

    def test_install_test_prefers_amd64(self):
        p = bm.plan({}, ANY_AND_ALL, "declared", "declared")
        self.assertEqual([(i["suite"], i["arch"], i["runner"]) for i in p["install"]],
                         [("trixie", "amd64", "ubuntu-24.04"), ("forky", "amd64", "ubuntu-24.04"),
                          ("sid", "amd64", "ubuntu-24.04"), ("raspbian-trixie", "armhf", "ubuntu-24.04-arm"),
                          ("raspbian-forky", "armhf", "ubuntu-24.04-arm")])


class Inputs(unittest.TestCase):
    def test_inputs_override_the_declaration(self):
        p = bm.plan({"architectures": "all"}, ANY_AND_ALL, "trixie raspbian-trixie", "amd64 armhf")
        self.assertEqual(p["suites"], ["trixie", "raspbian-trixie"])
        self.assertEqual(p["architectures"], "amd64 armhf all")
        self.assertEqual(jobs(p), [("trixie", "amd64", "ubuntu-24.04", "true"),
                                   ("trixie", "armhf", "ubuntu-24.04-arm", "false"),
                                   ("raspbian-trixie", "armhf", "ubuntu-24.04-arm", "true")])

    def test_default_keywords(self):
        p = bm.plan({"suites": ["bookworm"]}, ANY_ONLY, "default", "any")
        self.assertEqual(p["suites"], ["trixie", "forky", "sid", "raspbian-trixie", "raspbian-forky"])


class Refuses(unittest.TestCase):
    def check(self, decl, control, suites="declared", archs="declared", match=""):
        with self.assertRaisesRegex(bm.Error, match):
            bm.plan(decl, control, suites, archs)

    def test_unknown_suite(self):
        self.check({}, ANY_ONLY, suites="trixie stable", match="stable")

    def test_raspbian_sid(self):
        self.check({}, ANY_ONLY, suites="raspbian-sid", match="raspbian-sid")

    def test_unknown_arch(self):
        self.check({}, ANY_ONLY, archs="amd64 armel", match="armel")

    def test_raspbian_with_all(self):
        self.check({"architectures": "all"}, ALL_ONLY, suites="trixie raspbian-trixie",
                   match="Architecture: all")

    def test_raspbian_with_all_and_bundle_elsewhere(self):
        # A bundle for bookworm only doesn't make raspbian-trixie useful.
        decl = {"architectures": "all", "depends": [
            {"repo": "o/dep", "bundle": True, "suites": ["bookworm"], "reason": "r"}]}
        self.check(decl, ALL_ONLY, suites="trixie raspbian-trixie", match="unless it bundles")

    def test_raspbian_with_all_needs_its_debian_suite(self):
        decl = {"architectures": "all", "depends": [{"repo": "o/dep", "bundle": True, "reason": "r"}]}
        self.check(decl, ALL_ONLY, suites="trixie raspbian-forky", match="Debian suite of its codename")

    def test_raspbian_without_armhf(self):
        self.check({"architectures": ["arm64"]}, ANY_ONLY, suites="trixie raspbian-trixie", match="armhf")

    def test_all_but_arch_dependent_packages(self):
        self.check({"architectures": "all"}, ANY_AND_ALL, match="architecture-dependent")

    def test_architectures_but_only_all_packages(self):
        self.check({}, ALL_ONLY, match='architectures = "all"')

    def test_nothing_to_build(self):
        self.check({"suites": ["bookworm"], "architectures": ["riscv64"]}, ANY_ONLY, match="nothing")

    def test_duplicates(self):
        self.check({}, ANY_ONLY, suites="trixie trixie", match="twice")


class Control(unittest.TestCase):
    def test_architectures(self):
        text = """Source: x
Build-Depends: debhelper-compat (= 13),
 foo
Architecture: this is not a binary package's

Package: x-common
Architecture: all
Description: c
 Architecture: any (a description line, not a field)

Package: x
Architecture: any
Description: x

Package: x-pi
Architecture: arm64 armhf
Description: p
"""
        self.assertEqual(bm.control_architectures(text), ["all", "any", "arm64 armhf"])

    def test_whitespace_only_line_separates(self):
        # deb822(5): parsers may take a line of only spaces and tabs as a
        # paragraph separator, and apt-compliance.py does; the two must
        # agree, or build-deb.yml and PKG-ARCH read different packages.
        text = "Package: a\nArchitecture: any\n \t\nPackage: b\nArchitecture: all\n"
        self.assertEqual(bm.control_architectures(text), ["any", "all"])


class Command(unittest.TestCase):
    def test_hello_fixture(self):
        hello = SCRIPT.parent.parent / "tests/fixtures/hello"
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            subprocess.run([sys.executable, str(SCRIPT), "--source-dir", str(hello),
                            "--suites", "trixie raspbian-trixie", "--architectures", "amd64 armhf",
                            "--github-output", str(out)], check=True)
            lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
        self.assertEqual(lines["suites"], "trixie raspbian-trixie")
        self.assertEqual(lines["architectures"], "amd64 armhf all")
        self.assertEqual(len(json.loads(lines["build"])), 3)
        self.assertEqual(len(json.loads(lines["install"])), 2)

    def test_missing_declaration_is_the_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)
            (src / "debian").mkdir()
            (src / "debian/control").write_text("Source: x\n\nPackage: x\nArchitecture: all\n")
            out = src / "out"
            r = subprocess.run([sys.executable, str(SCRIPT), "--source-dir", str(src),
                                "--github-output", str(out)], capture_output=True, text=True)
            # No declaration means the default architectures, and an
            # Architecture: all only tree can't build those.
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('architectures = "all"', r.stderr)


if __name__ == "__main__":
    unittest.main()
