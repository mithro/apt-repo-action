"""tests/pick-stale-raspbian.py's choice, offline, on small Packages/Sources."""
import importlib.util
import shutil
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("pick", Path(__file__).parent / "pick-stale-raspbian.py")
pick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pick)


def binary(pkg, version, source, arch="armhf"):
    return f"Package: {pkg}\nVersion: {version}\nArchitecture: {arch}\nSource: {source}\n"


def source(name, version, size, build_depends=""):
    bd = f"Build-Depends: {build_depends}\n" if build_depends else ""
    return (f"Package: {name}\nVersion: {version}\n{bd}Files:\n"
            f" 0123456789abcdef {size} {name}_{version}.dsc\n")


def provider(pkg, version, provides):
    return f"Package: {pkg}\nVersion: {version}\nArchitecture: armhf\nProvides: {provides}\n"


@unittest.skipUnless(shutil.which("dpkg"), "needs dpkg --compare-versions")
class Pick(unittest.TestCase):
    def test_prefers_schemars_derive_while_stale(self):
        packages = ["\n".join([binary("librust-schemars-derive-dev", "1.2.2-1", "rust-schemars-derive"),
                               binary("librust-tiny-dev", "0.1-1", "rust-tiny")])]
        sources = ["\n".join([source("rust-schemars-derive", "1.2.2-2", 90000),
                              source("rust-tiny", "0.1-2", 10)])]
        self.assertEqual(pick.pick(packages, sources),
                         {"source": "rust-schemars-derive", "binary": "librust-schemars-derive-dev",
                          "version": "1.2.2-2"})

    def test_smallest_other_once_it_is_built(self):
        packages = ["\n".join([binary("librust-schemars-derive-dev", "1.2.2-2", "rust-schemars-derive"),
                               binary("librust-big-dev", "1.0-1", "rust-big"),
                               binary("librust-small-dev", "1.0-1", "rust-small")])]
        sources = ["\n".join([source("rust-schemars-derive", "1.2.2-2", 90000),
                              source("rust-big", "1.0-2", 500000),
                              source("rust-small", "1.0-2", 3000)])]
        self.assertEqual(pick.pick(packages, sources)["source"], "rust-small")

    def test_newest_binary_across_suite_and_staging(self):
        # forky has the old binary, staging the new one: not stale.
        packages = [binary("librust-x-dev", "1.0-1", "rust-x"), binary("librust-x-dev", "1.0-2", "rust-x")]
        sources = [source("rust-x", "1.0-1", 1), source("rust-x", "1.0-2", 1)]
        self.assertIsNone(pick.pick(packages, sources))

    def test_binnmu_and_arch_all_and_non_rust_are_not_picked(self):
        packages = ["\n".join([binary("librust-x-dev", "1.0-1+b3", "rust-x"),
                               binary("librust-y-dev", "1.0-1", "rust-y", arch="all"),
                               binary("libfoo-dev", "1.0-1", "foo")])]
        sources = ["\n".join([source("rust-x", "1.0-1", 1), source("rust-y", "1.0-2", 1),
                              source("foo", "1.0-2", 1)])]
        self.assertIsNone(pick.pick(packages, sources))

    def test_source_version_in_binary_source_field(self):
        # "Source: rust-z (1.0-1)": built from 1.0-1, whatever the binary's version.
        packages = [binary("librust-z-dev", "1.0-1+b1", "rust-z (1.0-1)")]
        sources = [source("rust-z", "1.0-3", 1)]
        self.assertEqual(pick.pick(packages, sources)["version"], "1.0-3")


    def test_skips_a_crate_whose_build_depends_cannot_be_met(self):
        packages = ["\n".join([binary("librust-small-dev", "1.0-1", "rust-small"),
                               binary("librust-big-dev", "1.0-1", "rust-big"),
                               binary("dh-cargo", "31", "dh-cargo")])]
        sources = ["\n".join([source("rust-small", "1.0-2", 10, "dh-cargo, librust-missing-dev (>= 2)"),
                              source("rust-big", "1.0-2", 900, "dh-cargo")])]
        self.assertEqual(pick.pick(packages, sources)["source"], "rust-big")

    def test_none_when_no_candidate_is_buildable(self):
        packages = [binary("librust-x-dev", "1.0-1", "rust-x")]
        sources = [source("rust-x", "1.0-2", 1, "librust-missing-dev")]
        self.assertIsNone(pick.pick(packages, sources))


@unittest.skipUnless(shutil.which("dpkg"), "needs dpkg --compare-versions")
class BuildDepends(unittest.TestCase):
    archive = pick.Archive(["\n".join([
        binary("dh-cargo", "31", "dh-cargo"),
        provider("librust-foo-dev", "1.5-1", "librust-foo-1+default-dev (= 1.5-1), librust-foo+std-dev"),
    ])])

    def missing(self, bd):
        return pick.buildable(bd, self.archive)

    def test_plain_and_versioned(self):
        self.assertEqual(self.missing("dh-cargo, dh-cargo (>= 30)"), [])
        self.assertEqual(self.missing("dh-cargo (>= 32)"), ["dh-cargo (>= 32)"])

    def test_alternatives(self):
        self.assertEqual(self.missing("librust-nope-dev | dh-cargo"), [])

    def test_provides(self):
        # A versioned Provides meets a versioned relation; an unversioned one doesn't.
        self.assertEqual(self.missing("librust-foo-1+default-dev (>= 1.4)"), [])
        self.assertEqual(self.missing("librust-foo+std-dev"), [])
        self.assertEqual(self.missing("librust-foo+std-dev (>= 1)"), ["librust-foo+std-dev (>= 1)"])

    def test_nocheck_profile(self):
        # Test-only dependencies don't count for a nocheck rebuild.
        self.assertEqual(self.missing("dh-cargo, librust-nope-dev <!nocheck>"), [])
        self.assertEqual(self.missing("librust-nope-dev <nocheck>"), ["librust-nope-dev <nocheck>"])

    def test_architecture_restrictions(self):
        self.assertEqual(self.missing("librust-nope-dev [amd64 arm64]"), [])
        self.assertEqual(self.missing("librust-nope-dev [!armhf]"), [])
        self.assertEqual(self.missing("librust-nope-dev [armhf]"), ["librust-nope-dev [armhf]"])
        self.assertEqual(self.missing("librust-nope-dev [!amd64]"), ["librust-nope-dev [!amd64]"])


if __name__ == "__main__":
    unittest.main()
