"""Tests for scripts/bundle-depends.py (docs/packaging.md, "Bundling a
dependency repository"). No network: the dependency repository is a dict of
URLs, signed with a throwaway key, so gpgv really checks it.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
Needs gpg, gpgv, dpkg and dpkg-deb.
"""
import gzip
import hashlib
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/bundle-depends.py"
_loader = importlib.machinery.SourceFileLoader("bundle_depends", str(SCRIPT))
bd = importlib.util.module_from_spec(importlib.util.spec_from_loader("bundle_depends", _loader))
_loader.exec_module(bd)

SITE = "https://owner.example/dep-repo"
KEY_URL = f"{SITE}/dep-repo.gpg"


def run(*cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


class Site:
    """A signed flat dependency repository, served from a dict."""

    def __init__(self, tmp: Path):
        self.files: dict[str, bytes] = {}
        self.home = tmp / "gnupg"
        self.home.mkdir(mode=0o700, parents=True)
        self.env = {**os.environ, "GNUPGHOME": str(self.home)}
        run("gpg", "--batch", "--passphrase", "", "--quick-gen-key", "dep <dep@invalid>",
            "ed25519", "sign", "never", env=self.env)
        self.files[KEY_URL] = run("gpg", "--export", env=self.env).stdout
        self.tmp = tmp

    def publish(self, suite: str, packages: list[dict], codename: str | None = None,
                gz_only: bool = False) -> None:
        stanzas = []
        for p in packages:
            name = f"{p['Package']}_{p['Version']}_{p['Architecture']}.deb"
            fields = {k: v for k, v in p.items() if k not in ("data", "control")}
            if "data" in p:
                data = p["data"]
            else:
                # A real package whose control file is the stanza's, or,
                # with "control", deliberately not.
                built = deb_bytes(self.tmp / "pool", {**fields, **p.get("control", {})})
                data = built
            self.files[f"{SITE}/{suite}/{name}"] = data
            fields.update(Filename=f"./{name}", Size=str(len(data)), SHA256=hashlib.sha256(data).hexdigest())
            stanzas.append("".join(f"{k}: {v}\n" for k, v in fields.items()))
        index = "\n".join(stanzas).encode()
        listed = {"Packages.gz": gzip.compress(index)} if gz_only else {"Packages": index}
        for name, data in listed.items():
            self.files[f"{SITE}/{suite}/{name}"] = data
        release = f"Origin: dep-repo\nCodename: {codename or suite}\nSHA256:\n" + "".join(
            f" {hashlib.sha256(d).hexdigest()} {len(d)} {n}\n" for n, d in listed.items())
        (self.tmp / "Release").write_text(release)
        run("gpg", "--batch", "--yes", "--clearsign", "-o", str(self.tmp / "InRelease"),
            str(self.tmp / "Release"), env=self.env)
        self.files[f"{SITE}/{suite}/InRelease"] = (self.tmp / "InRelease").read_bytes()

    def get(self, url, headers=None):
        if url not in self.files:
            raise bd.Error(f"{url}: HTTP 404 Not Found")
        return self.files[url]


def deb_bytes(work: Path, fields: dict) -> bytes:
    """A real .deb whose control file has these fields."""
    work.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=work))
    (root / "DEBIAN").mkdir()
    control = {"Maintainer": "t <t@invalid>", "Description": "t", **fields}
    (root / "DEBIAN/control").write_text("".join(f"{k}: {v}\n" for k, v in control.items()))
    out = root.with_suffix(".deb")
    run("dpkg-deb", "--build", "--root-owner-group", str(root), str(out))
    return out.read_bytes()


def deb(dest: Path, package: str, arch: str = "all", depends: str = "", version: str = "1.0") -> Path:
    root = dest / f"root-{package}-{arch}"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "DEBIAN/control").write_text(
        f"Package: {package}\nVersion: {version}\nArchitecture: {arch}\n"
        f"Maintainer: t <t@invalid>\nDescription: t\n" + (f"Depends: {depends}\n" if depends else ""))
    out = dest / f"{package}_{version}_{arch}.deb"
    run("dpkg-deb", "--build", "--root-owner-group", str(root), str(out))
    run("rm", "-r", str(root))
    return out


def entries(bundle=True, **extra):
    return bd.apt_sources.validate([{"repo": "owner/dep-repo", "bundle": bundle, "reason": "r", **extra}])


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.site = Site(self.tmp)
        self.debs = self.tmp / "debs"
        self.debs.mkdir()
        p1 = mock.patch.object(bd.apt_sources, "http_get", self.site.get)
        p2 = mock.patch.object(bd.apt_sources, "pages_site", lambda repo: SITE)
        p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def fetch(self, suite="trixie", arch=None, ents=None):
        return bd.fetch(ents or entries(), suite, self.debs, self.debs, arch)

    def names(self):
        return sorted(p.name for p in self.debs.glob("*.deb"))


class Select(Base):
    def test_closure_newest_and_nothing_unneeded(self):
        self.site.publish("trixie", [
            {"Package": "liba", "Version": "1.0", "Architecture": "all"},
            {"Package": "liba", "Version": "2.0", "Architecture": "all", "Depends": "libc (>= 1)"},
            {"Package": "libc", "Version": "1.0", "Architecture": "all"},
            {"Package": "unrelated", "Version": "1.0", "Architecture": "all"}])
        deb(self.debs, "ours", depends="liba (>= 2) | libz, debianpkg")
        lines = self.fetch()
        self.assertEqual(self.names(), ["liba_2.0_all.deb", "libc_1.0_all.deb", "ours_1.0_all.deb"])
        self.assertIn("liba 2.0 all from owner/dep-repo", lines)
        self.assertEqual((self.debs / bd.OVERRIDE).read_text(),
                         "liba Bundled-From owner/dep-repo\nlibc Bundled-From owner/dep-repo\n")

    def test_architectures(self):
        self.site.publish("trixie", [
            {"Package": "libx", "Version": "1", "Architecture": a} for a in ("amd64", "arm64", "riscv64")])
        deb(self.debs, "ours-bin", arch="amd64", depends="libx")
        self.fetch()
        self.assertEqual(self.names(), ["libx_1_amd64.deb", "ours-bin_1.0_amd64.deb"])

    def test_all_package_takes_every_architecture(self):
        self.site.publish("trixie", [
            {"Package": "libx", "Version": "1", "Architecture": a} for a in ("amd64", "arm64")])
        deb(self.debs, "ours", depends="libx")
        self.fetch()
        self.assertEqual(self.names(), ["libx_1_amd64.deb", "libx_1_arm64.deb", "ours_1.0_all.deb"])

    def test_arch_restricts(self):
        self.site.publish("trixie", [
            {"Package": "libx", "Version": "1", "Architecture": a} for a in ("amd64", "arm64")])
        deb(self.debs, "ours", depends="libx")
        self.fetch(arch="arm64")
        self.assertEqual(self.names(), ["libx_1_arm64.deb", "ours_1.0_all.deb"])

    def test_provides_and_gz_index(self):
        self.site.publish("trixie", [
            {"Package": "real", "Version": "1", "Architecture": "all", "Provides": "virt (= 1)"}], gz_only=True)
        deb(self.debs, "ours", depends="virt")
        self.fetch()
        self.assertIn("real_1_all.deb", self.names())

    def test_nothing_needed(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}])
        deb(self.debs, "ours", depends="debianpkg")
        self.assertEqual(self.fetch(), [])
        self.assertEqual(self.names(), ["ours_1.0_all.deb"])

    def test_not_bundled_and_other_suite_do_nothing(self):
        deb(self.debs, "ours", depends="liba")
        self.assertEqual(self.fetch(ents=entries(bundle=False)), [])
        self.assertEqual(self.fetch(ents=entries(suites=["bookworm"])), [])

    def test_name_clash(self):
        self.site.publish("trixie", [{"Package": "ours", "Version": "9", "Architecture": "all"}])
        deb(self.debs, "ours")
        with self.assertRaisesRegex(bd.Error, "both ours and a bundled"):
            self.fetch()


class Verify(Base):
    def setUp(self):
        super().setUp()
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}])
        deb(self.debs, "ours", depends="liba")

    def refuses(self, message):
        with self.assertRaisesRegex(bd.Error, message):
            self.fetch()
        self.assertEqual(self.names(), ["ours_1.0_all.deb"])

    def test_good(self):
        self.fetch()
        self.assertIn("liba_1_all.deb", self.names())

    def test_tampered_deb(self):
        self.site.files[f"{SITE}/trixie/liba_1_all.deb"] = b"evil"
        self.refuses("doesn't match its Packages index")

    def test_tampered_packages(self):
        self.site.files[f"{SITE}/trixie/Packages"] += b"\n"
        self.refuses("doesn't match its InRelease")

    def test_tampered_inrelease(self):
        f = f"{SITE}/trixie/InRelease"
        self.site.files[f] = self.site.files[f].replace(b"Origin: dep-repo", b"Origin: evil-repo")
        self.refuses("doesn't verify")

    def test_other_key(self):
        other = Site(self.tmp / "other")
        self.site.files[KEY_URL] = other.files[KEY_URL]
        self.refuses("doesn't verify")

    def test_another_suites_release(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}], codename="forky")
        self.refuses("its Release is for 'forky', not trixie")

    def test_unreachable(self):
        del self.site.files[f"{SITE}/trixie/InRelease"]
        self.refuses("404")


class Stale(Base):
    """What our next publish would bundle that the live site doesn't have."""

    OURS = "Package: ours\nVersion: 1\nArchitecture: all\nDepends: liba\n"

    def live(self, *bundled, ours=OURS):
        text = ours + "".join(
            f"\nPackage: liba\nVersion: {v}\nArchitecture: all\nBundled-From: owner/dep-repo\n"
            for v in bundled)
        self.site.files["https://us.example/ours/trixie/Packages"] = text.encode()

    def stale(self):
        return bd.stale(entries(), ["trixie"], "https://us.example/ours")

    def test_newer_upstream(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": "2", "Architecture": "all"}])
        self.live("1")
        self.assertEqual(self.stale(), ["trixie: would bundle liba 2 (all) from owner/dep-repo, "
                                        "which the site doesn't have"])

    def test_current(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}])
        self.live("1")
        self.assertEqual(self.stale(), [])

    def test_needed_but_not_bundled_yet(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}])
        self.live()
        self.assertEqual(len(self.stale()), 1)

    def test_nothing_needed_is_never_stale(self):
        # A `bundle` entry for a suite where nothing of ours needs anything
        # from it: a scheduled check must not rebuild every time (M2).
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}])
        self.live(ours="Package: ours\nVersion: 1\nArchitecture: all\nDepends: debianpkg\n")
        self.assertEqual(self.stale(), [])

    def test_follows_version_constraints(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": v, "Architecture": "all"} for v in ("1", "2")])
        self.live("1", ours="Package: ours\nVersion: 1\nArchitecture: all\nDepends: liba (<< 2)\n")
        self.assertEqual(self.stale(), [])


class Versions(Base):
    """A relation takes the newest version that satisfies it (L1)."""

    def test_constraint_picks_an_older_version(self):
        self.site.publish("trixie", [{"Package": "libfoo", "Version": v, "Architecture": "all"}
                                     for v in ("1.0", "2.0")])
        deb(self.debs, "ours", depends="libfoo (<< 2)")
        self.fetch()
        self.assertEqual(self.names(), ["libfoo_1.0_all.deb", "ours_1.0_all.deb"])

    def test_two_relations_two_versions(self):
        self.site.publish("trixie", [{"Package": "libfoo", "Version": v, "Architecture": "all"}
                                     for v in ("1.0", "2.0")])
        deb(self.debs, "ours", depends="libfoo (<< 2)")
        deb(self.debs, "ours-too", depends="libfoo (>= 2)")
        self.fetch()
        self.assertEqual(self.names(), ["libfoo_1.0_all.deb", "libfoo_2.0_all.deb",
                                        "ours-too_1.0_all.deb", "ours_1.0_all.deb"])

    def test_old_style_operators_and_epochs(self):
        self.site.publish("trixie", [{"Package": "libfoo", "Version": v, "Architecture": "all"}
                                     for v in ("1:0.9", "2.0")])
        deb(self.debs, "ours", depends="libfoo (> 1:0)")   # dpkg's old >=
        self.fetch()
        self.assertEqual(self.names(), ["libfoo_1:0.9_all.deb", "ours_1.0_all.deb"])

    def test_versioned_provides(self):
        self.site.publish("trixie", [
            {"Package": "real", "Version": "1", "Architecture": "all", "Provides": "virt (= 3)"},
            {"Package": "other", "Version": "1", "Architecture": "all", "Provides": "virt"}])
        deb(self.debs, "ours", depends="virt (>= 2)")
        self.fetch()
        # Only a versioned Provides satisfies a versioned relation.
        self.assertEqual(self.names(), ["ours_1.0_all.deb", "real_1_all.deb"])

    def test_unsatisfiable_fails(self):
        self.site.publish("trixie", [{"Package": "libfoo", "Version": "1.0", "Architecture": "all"}])
        deb(self.debs, "ours", depends="libfoo (>= 2)")
        with self.assertRaisesRegex(bd.Error, r"ours needs libfoo \(>= 2\), and the bundled repository "
                                              r"has libfoo only at 1.0"):
            self.fetch()

    def test_unsatisfiable_alternative_is_left_to_debian(self):
        self.site.publish("trixie", [{"Package": "libfoo", "Version": "1.0", "Architecture": "all"}])
        deb(self.debs, "ours", depends="libfoo (>= 2) | debianpkg")
        self.assertEqual(self.fetch(), [])


class Control(Base):
    """The file's own control fields must be what its stanza says (M1)."""

    def test_a_file_claiming_to_be_ours(self):
        # The stanza says libfoo; the file (whose hash the stanza carries)
        # says it is `ours`, at a higher version: it would enter our signed
        # index as a second, unmarked `ours`.
        self.site.publish("trixie", [{"Package": "libfoo", "Version": "1", "Architecture": "all",
                                      "control": {"Package": "ours", "Version": "99"}}])
        deb(self.debs, "ours", depends="libfoo")
        with self.assertRaisesRegex(bd.Error, r"control file isn't what its index says: Package: 'ours'"):
            self.fetch()
        self.assertEqual(self.names(), ["ours_1.0_all.deb"])

    def test_other_relations(self):
        self.site.publish("trixie", [{"Package": "libfoo", "Version": "1", "Architecture": "all",
                                      "control": {"Depends": "evil"}}])
        deb(self.debs, "ours", depends="libfoo")
        with self.assertRaisesRegex(bd.Error, "Depends: 'evil' in the file, None in its index"):
            self.fetch()

    def test_not_a_package(self):
        self.site.publish("trixie", [{"Package": "libfoo", "Version": "1", "Architecture": "all",
                                      "data": b"not a deb"}])
        deb(self.debs, "ours", depends="libfoo")
        with self.assertRaisesRegex(bd.Error, "not a Debian package"):
            self.fetch()


class Index(Base):
    def test_local_source_index(self):
        # For an install test: apt chooses among the bundled files (L2).
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}])
        deb(self.debs, "ours", depends="liba")
        dest = self.tmp / "bundled"
        bd.fetch(entries(), "trixie", self.debs, dest, None, index=True)
        [st] = bd.stanzas((dest / "Packages").read_text())
        self.assertEqual((st["Package"], st["Filename"], st["Bundled-From"]),
                         ("liba", "./liba_1_all.deb", "owner/dep-repo"))

    def test_empty_when_nothing_bundled(self):
        deb(self.debs, "ours")
        dest = self.tmp / "bundled"
        bd.fetch(entries(bundle=False), "trixie", self.debs, dest, None, index=True)
        self.assertEqual((dest / "Packages").read_text(), "")


class Owner(unittest.TestCase):
    def test_owner_required_for_bundle_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            decl = Path(tmp) / "d.toml"
            decl.write_text('[[depends]]\nrepo = "someone/dep"\nbundle = true\nreason = "r"\n')
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_REPOSITORY"}
            args = ["python3", str(SCRIPT), "stale", "--declaration", str(decl),
                    "--suites", "trixie", "--site", "https://x.invalid"]
            r = subprocess.run(args, capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1)
            self.assertIn("pass --owner", r.stderr)
            r = subprocess.run(args + ["--owner", "mithro"], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1)
            self.assertIn("someone/dep isn't mithro's", r.stderr)


class Relations(unittest.TestCase):
    def test_names(self):
        self.assertEqual(bd.relation_names("a (>= 1), b:any | c [amd64] <!nocheck>, python3:native"),
                         ["a", "b", "c", "python3"])


if __name__ == "__main__":
    unittest.main()
