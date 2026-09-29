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
            data = p.get("data", f"{p['Package']} {p['Version']} {p['Architecture']}".encode())
            name = f"{p['Package']}_{p['Version']}_{p['Architecture']}.deb"
            self.files[f"{SITE}/{suite}/{name}"] = data
            fields = {k: v for k, v in p.items() if k != "data"}
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
    def live(self, text):
        self.site.files["https://us.example/ours/trixie/Packages"] = textwrap.dedent(text).encode()

    def stale(self):
        return bd.stale(entries(), ["trixie"], "https://us.example/ours")

    def test_newer_upstream(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": "2", "Architecture": "all"}])
        self.live("""\
            Package: liba
            Version: 1
            Architecture: all
            Bundled-From: owner/dep-repo
            """)
        self.assertEqual(self.stale(), ["trixie: owner/dep-repo has liba 2 (all), we bundle 1"])

    def test_current(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}])
        self.live("""\
            Package: liba
            Version: 1
            Architecture: all
            Bundled-From: owner/dep-repo
            """)
        self.assertEqual(self.stale(), [])

    def test_nothing_bundled_yet(self):
        self.site.publish("trixie", [{"Package": "liba", "Version": "1", "Architecture": "all"}])
        self.live("Package: ours\nVersion: 1\nArchitecture: all\n")
        self.assertEqual(self.stale(), ["trixie: nothing bundled from owner/dep-repo yet"])


class Relations(unittest.TestCase):
    def test_names(self):
        self.assertEqual(bd.relation_names("a (>= 1), b:any | c [amd64] <!nocheck>, python3:native"),
                         ["a", "b", "c", "python3"])


if __name__ == "__main__":
    unittest.main()
