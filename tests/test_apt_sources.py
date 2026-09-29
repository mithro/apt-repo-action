"""Tests for scripts/apt-sources.py against docs/packaging.md ("The
declaration", `[[depends]]`). No network: the GitHub API and the key
downloads are replaced.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
The keyring test needs gpg.
"""
import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/apt-sources.py"
_loader = importlib.machinery.SourceFileLoader("apt_sources", str(SCRIPT))
aps = importlib.util.module_from_spec(importlib.util.spec_from_loader("apt_sources", _loader))
_loader.exec_module(aps)

OURS = {"repo": "someone/dep-backport", "suites": ["bookworm"], "reason": "dep >= 2 isn't in bookworm"}
THIRD = {"name": "example", "url": "https://example.org/debian", "suite": "{codename}",
         "components": ["main", "contrib"], "key": "https://example.org/key.asc",
         "suites": ["trixie", "raspbian-trixie"], "reason": "a third-party library"}
FLAT = {"name": "flat", "url": "https://example.net/{suite}/", "suite": "./",
        "key": "https://example.net/flat.gpg", "reason": "flat, one directory per suite"}
# A public key packet header (old format, tag 6) and some body: all
# check_public_key looks at is the first packet's tag.
BINARY_KEY = bytes([0x99, 0x00, 0x03, 0x04, 0x01, 0x02])


def armour(raw: bytes, headers: str = "", flip_crc: bool = False) -> bytes:
    b64 = base64.b64encode(raw).decode()
    crc = base64.b64encode((aps._crc24(raw) ^ flip_crc).to_bytes(3, "big")).decode()
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return ("-----BEGIN PGP PUBLIC KEY BLOCK-----\n" + headers + "\n" + "\n".join(lines)
            + f"\n={crc}\n-----END PGP PUBLIC KEY BLOCK-----\n").encode()


class Validate(unittest.TestCase):
    def bad(self, entry, message):
        with self.assertRaises(aps.Error) as e:
            aps.validate([entry])
        self.assertIn(message, str(e.exception))

    def test_both_forms(self):
        ours, third, flat = aps.validate([OURS, THIRD, FLAT])
        self.assertEqual(ours["name"], "dep-backport")
        self.assertEqual(third["components"], ["main", "contrib"])
        self.assertIsNone(flat["suites"])

    def test_reason_required(self):
        self.bad({**OURS, "reason": " "}, "needs a `reason`")
        self.bad({k: v for k, v in THIRD.items() if k != "reason"}, "needs a `reason`")

    def test_unknown_suite(self):
        self.bad({**OURS, "suites": ["buster"]}, "unknown suite buster")
        self.bad({**OURS, "suites": []}, "non-empty list")

    def test_repo_form_takes_no_urls(self):
        self.bad({**OURS, "key": "https://x/k.gpg"}, "doesn't take key")
        self.bad({**OURS, "repo": "no-slash"}, '"owner/name"')

    def test_explicit_form_needs_its_keys(self):
        self.bad({k: v for k, v in THIRD.items() if k != "key"}, "or else key")

    def test_flat_and_components(self):
        self.bad({**FLAT, "suite": "{suite}"}, "flat repository (no components)")
        self.bad({**THIRD, "suite": "./"}, "has no components")

    def test_placeholders(self):
        self.bad({**THIRD, "suite": "{release}"}, "may only use {suite} and {codename}")
        self.bad({**FLAT, "url": "https://x/{0}/"}, "may only use")

    def test_names(self):
        self.bad({**THIRD, "name": "../evil"}, "`name` must be")
        with self.assertRaises(aps.Error) as e:
            aps.validate([THIRD, {**FLAT, "name": "example"}])
        self.assertIn("each needs its own keyring", str(e.exception))

    def test_key_must_be_https(self):
        self.bad({**FLAT, "key": "http://example.net/flat.gpg"}, "must be https://")
        # Except on this machine or its private network: the self-test.
        for host in ("127.0.0.1:8000", "172.17.0.1:8766", "[::1]"):
            aps.validate([{**FLAT, "key": f"http://{host}/k.gpg"}])

    def test_bundle(self):
        # docs/packaging.md, "Bundling a dependency repository".
        self.assertFalse(aps.validate([OURS])[0]["bundle"])
        self.assertTrue(aps.validate([{**OURS, "bundle": True}])[0]["bundle"])
        self.bad({**OURS, "bundle": "yes"}, "`bundle` is true or false")
        # Someone else's: never by accident, and only a flat repository.
        self.bad({**FLAT, "bundle": True}, 'bundle = "third-party"')
        self.assertTrue(aps.validate([{**FLAT, "bundle": "third-party"}])[0]["bundle"])
        self.bad({**THIRD, "bundle": "third-party"}, "only a flat repository can be bundled")


class Load(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def test_no_file_declares_nothing(self):
        self.assertEqual(aps.load(self.dir / "none.toml"), [])

    def test_no_depends(self):
        (self.dir / "a.toml").write_text('kind = "B"\n')
        self.assertEqual(aps.load(self.dir / "a.toml"), [])

    def test_depends_must_be_tables(self):
        (self.dir / "a.toml").write_text('depends = ["x"]\n')
        with self.assertRaises(aps.Error):
            aps.load(self.dir / "a.toml")

    def test_bad_toml(self):
        (self.dir / "a.toml").write_text("kind = \n")
        with self.assertRaises(aps.Error):
            aps.load(self.dir / "a.toml")


class Resolve(unittest.TestCase):
    """The `repo` form through a mocked GitHub API."""

    def setUp(self):
        self.calls = []
        patch = mock.patch.object(aps, "http_get", self.fake_get)
        patch.start()
        self.addCleanup(patch.stop)
        env = mock.patch.dict(os.environ, {"GH_TOKEN": "t0ken", "GITHUB_API_URL": "https://api.example"})
        env.start()
        self.addCleanup(env.stop)

    def fake_get(self, url, headers=None):
        self.calls.append((url, headers))
        if url == "https://api.example/repos/someone/dep-backport/pages":
            return json.dumps({"html_url": "https://pkgs.example.com/dep-backport/"}).encode()
        if url == "https://api.example/repos/someone/plain-http/pages":
            return json.dumps({"html_url": "http://pkgs.example.com/plain-http/"}).encode()
        if url == "https://api.example/repos/someone/private-http/pages":
            return json.dumps({"html_url": "http://172.17.0.1:8766/"}).encode()
        raise aps.Error(f"{url}: HTTP 404 Not Found")

    def test_ours(self):
        [r] = aps.resolve(aps.validate([OURS]), "bookworm")
        self.assertEqual(self.calls[0][1]["Authorization"], "Bearer t0ken")
        self.assertEqual(r["line"], "deb [signed-by=/etc/apt/keyrings/dep-backport.gpg] "
                                    "https://pkgs.example.com/dep-backport/bookworm/ ./")
        self.assertEqual(r["key"], "https://pkgs.example.com/dep-backport/dep-backport.gpg")
        self.assertEqual(r["policy"], ["https://pkgs.example.com/dep-backport/bookworm ./"])

    def test_suites_filter(self):
        self.assertEqual(aps.resolve(aps.validate([OURS]), "trixie"), [])
        self.assertEqual(self.calls, [])   # nothing applies, nothing looked up

    def test_no_pages_site(self):
        with self.assertRaises(aps.Error) as e:
            aps.resolve(aps.validate([{**OURS, "repo": "someone/nothing"}]), "bookworm")
        self.assertIn("can't find someone/nothing's GitHub Pages site", str(e.exception))

    def test_http_site_refused(self):
        # The key would be fetched over plain http. Not even a private
        # address: that exception is the explicit form's alone.
        for repo in ("someone/plain-http", "someone/private-http"):
            with self.subTest(repo=repo), self.assertRaises(aps.Error) as e:
                aps.resolve(aps.validate([{**OURS, "repo": repo}]), "bookworm")
            self.assertIn("must enforce HTTPS", str(e.exception))

    def test_explicit(self):
        third, flat = aps.resolve(aps.validate([THIRD, FLAT]), "raspbian-trixie")
        self.assertEqual(third["line"], "deb [signed-by=/etc/apt/keyrings/example.gpg] "
                                        "https://example.org/debian/ trixie main contrib")
        self.assertEqual(third["policy"], ["https://example.org/debian trixie/main",
                                           "https://example.org/debian trixie/contrib"])
        self.assertEqual(flat["line"], "deb [signed-by=/etc/apt/keyrings/flat.gpg] "
                                       "https://example.net/raspbian-trixie/ ./")
        self.assertEqual(self.calls, [])

    def test_unknown_build_suite(self):
        with self.assertRaises(aps.Error):
            aps.resolve(aps.validate([FLAT]), "jammy")
        self.assertEqual(aps.resolve([], "jammy"), [])   # nothing declared: anything goes


class Keys(unittest.TestCase):
    def test_binary_unchanged(self):
        self.assertEqual(aps.dearmor(BINARY_KEY), BINARY_KEY)

    def test_armoured(self):
        self.assertEqual(aps.dearmor(armour(BINARY_KEY)), BINARY_KEY)
        self.assertEqual(aps.dearmor(armour(BINARY_KEY, "Comment: x\nVersion: y\n")), BINARY_KEY)

    def test_bad_checksum(self):
        with self.assertRaisesRegex(aps.Error, "checksum"):
            aps.dearmor(armour(BINARY_KEY, flip_crc=True))

    def test_public_key_packets(self):
        aps.check_public_key(BINARY_KEY)
        aps.check_public_key(bytes([0xC6, 0x03, 0x04]))           # new format, tag 6
        for not_a_key in (b"<html>", bytes([0x95, 0x01]), b""):   # HTML, a secret key, nothing
            with self.assertRaises(aps.Error):
                aps.check_public_key(not_a_key)

    @unittest.skipUnless(shutil.which("gpg"), "needs gpg")
    def test_matches_gpg(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        home.chmod(0o700)
        env = {**os.environ, "GNUPGHOME": str(home)}

        def gpg(*a):
            return subprocess.run(["gpg", "--batch", "--passphrase", "", *a], env=env,
                                  check=True, capture_output=True).stdout
        gpg("--quick-gen-key", "apt-sources test <test@invalid>", "ed25519", "sign", "never")
        binary, armoured = gpg("--export"), gpg("--armor", "--export")
        self.addCleanup(subprocess.run, ["gpgconf", "--kill", "gpg-agent"], env=env)
        self.assertTrue(armoured.startswith(aps.ARMOUR_BEGIN))
        self.assertEqual(aps.dearmor(armoured), binary)
        aps.check_public_key(binary)


class Write(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def test_files(self):
        keys = {"https://example.org/key.asc": armour(BINARY_KEY), "https://example.net/flat.gpg": BINARY_KEY}
        with mock.patch.object(aps, "http_get", lambda url, headers=None: keys[url]):
            aps.write(aps.resolve(aps.validate([THIRD, FLAT]), "trixie"), self.dir)
        self.assertEqual((self.dir / "keyrings/example.gpg").read_bytes(), BINARY_KEY)
        self.assertEqual((self.dir / "keyrings/flat.gpg").read_bytes(), BINARY_KEY)
        self.assertIn("deb [signed-by=/etc/apt/keyrings/flat.gpg] https://example.net/trixie/ ./\n",
                      (self.dir / "sources.list.d/flat.list").read_text())
        self.assertEqual((self.dir / "checks").read_text(),
                         "example https://example.org/debian trixie/main\n"
                         "example https://example.org/debian trixie/contrib\n"
                         "flat https://example.net/trixie ./\n")
        self.assertTrue(os.access(self.dir / "install.sh", os.X_OK))

    def test_key_not_found_fails(self):
        def missing(url, headers=None):
            raise aps.Error(f"{url}: HTTP 404 Not Found")
        with mock.patch.object(aps, "http_get", missing):
            with self.assertRaisesRegex(aps.Error, "flat: can't use the key https://example.net/flat.gpg"):
                aps.write(aps.resolve(aps.validate([FLAT]), "trixie"), self.dir)
        self.assertFalse((self.dir / "install.sh").exists())

    def test_not_a_key_fails(self):
        with mock.patch.object(aps, "http_get", lambda url, headers=None: b"<html>404</html>"):
            with self.assertRaisesRegex(aps.Error, "not an OpenPGP key"):
                aps.write(aps.resolve(aps.validate([FLAT]), "trixie"), self.dir)

    def test_nothing_declared(self):
        # No declaration: `write` prints 0 and install.sh does nothing, as
        # any user, so build-deb behaves exactly as without [[depends]].
        r = subprocess.run(["python3", str(SCRIPT), "write", "--declaration", str(self.dir / "none.toml"),
                            "--suite", "trixie", "--dest", str(self.dir / "out")],
                           capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout, "0\n")
        self.assertEqual((self.dir / "out/checks").read_text(), "")
        r = subprocess.run(["sh", str(self.dir / "out/install.sh")], capture_output=True, text=True, check=True)
        self.assertIn("no extra apt repositories", r.stdout)

    def test_unbundled(self):
        # An install test that gets a bundled repository's packages from the
        # bundle adds only the others.
        (self.dir / "d.toml").write_text(
            '[[depends]]\nname = "flat"\nurl = "https://example.net/{suite}/"\nsuite = "./"\n'
            'key = "https://example.net/flat.gpg"\nbundle = "third-party"\nreason = "r"\n')
        r = subprocess.run(["python3", str(SCRIPT), "resolve", "--unbundled", "--declaration",
                            str(self.dir / "d.toml"), "--suite", "trixie"],
                           capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(r.stdout), [])

    def test_cli_error(self):
        (self.dir / "a.toml").write_text('[[depends]]\nrepo = "a/b"\n')
        r = subprocess.run(["python3", str(SCRIPT), "check", "--declaration", str(self.dir / "a.toml")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs a `reason`", r.stderr)


if __name__ == "__main__":
    unittest.main()
