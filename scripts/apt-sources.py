#!/usr/bin/env python3
"""The extra apt repositories a build depends on (docs/packaging.md, "The
declaration": `[[depends]]`).

A repository whose build or install needs packages Debian doesn't have for a
suite declares where they come from in `.github/apt-packaging.toml`:

    [[depends]]                       # one of ours (docs/conventions.md)
    repo = "owner/name"
    suites = ["bookworm"]             # optional: only these suites
    reason = "why"

    [[depends]]                       # anything else
    name = "example"                  # /etc/apt/keyrings/<name>.gpg
    url = "https://example.org/debian"   # a flat one: ".../{suite}/"
    suite = "{suite}"                 # or "{codename}", "stable"; "./" when flat
    components = ["main"]             # omit for a flat repository
    key = "https://example.org/key.gpg"
    suites = ["trixie", "forky"]
    reason = "why"

`repo` is resolved through the GitHub API (`repos/<repo>/pages`, html_url) to
the repository's site: the source is `<site>/<suite>/ ./` and the key
`<site>/<name>.gpg`. Nothing about any owner is built in.

    apt-sources.py check   --declaration F                # validate, no network
    apt-sources.py resolve --declaration F --suite S      # print what applies, as JSON
    apt-sources.py write   --declaration F --suite S --dest DIR

`write` resolves, fetches every key (dearmouring an armoured one), and fills
DIR with `keyrings/<name>.gpg`, `sources.list.d/<name>.list`, `sources.json`
and `install.sh`. `sh DIR/install.sh`, as root in any Debian container, puts
them in /etc/apt, runs `apt-get update` and fails unless every one of them was
fetched and its signature verified. It needs nothing but apt, so a bare
container can run it. Nothing applies: DIR gets an install.sh that does
nothing, and `write` prints 0.

The API is read with $GH_TOKEN or $GITHUB_TOKEN, at $GITHUB_API_URL.

Standard library only: it runs on the runner and in debian containers.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import shutil
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DECLARATION = ".github/apt-packaging.toml"
# The suites a build can be for (docs/packaging.md, "Suites"). Keep in step
# with scripts/deb-version.py when Debian makes a release.
CODENAMES = ["bookworm", "trixie", "forky", "sid"]
KNOWN_SUITES = CODENAMES + [f"raspbian-{c}" for c in CODENAMES if c != "sid"]
OURS = {"repo", "suites", "reason"}
EXPLICIT = {"name", "url", "suite", "components", "key", "suites", "reason"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")   # what apt reads in sources.list.d
REPO = re.compile(r"[A-Za-z0-9-]+/([A-Za-z0-9_.-]+)")
ARMOUR_BEGIN = b"-----BEGIN PGP PUBLIC KEY BLOCK-----"
ARMOUR_END = b"-----END PGP PUBLIC KEY BLOCK-----"
KEYRINGS = "/etc/apt/keyrings"
INSTALL_SH = Path(__file__).resolve().parent / "apt-sources-install.sh"


class Error(Exception):
    """A problem with the declaration or a source, for the user to fix."""


# --------------------------------------------------------------------------
# The declaration.

def load(path: Path) -> list[dict]:
    """The [[depends]] tables of a declaration; none if there is no file."""
    if not path.is_file():
        return []
    try:
        decl = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise Error(f"{path} doesn't parse: {e}") from None
    deps = decl.get("depends", [])
    if not isinstance(deps, list) or not all(isinstance(d, dict) for d in deps):
        raise Error(f"{path}: `depends` must be an array of tables, [[depends]]")
    return deps


def _url_ok(url: object, what: str, where: str) -> str:
    if not isinstance(url, str) or not re.fullmatch(r"https?://\S+", url):
        raise Error(f"{where}: {what} must be an http:// or https:// URL, not {url!r}")
    return url


def _key_url_ok(url: object, where: str) -> str:
    """The key is what makes the source trustworthy: https only, except for
    an address that never leaves the machine or its private network (the
    self-test's throwaway repository)."""
    _url_ok(url, "key", where)
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https":
        return url
    try:
        ip = ipaddress.ip_address(u.hostname or "")
    except ValueError:
        ip = None
    if ip is None or not (ip.is_loopback or ip.is_private):
        raise Error(f"{where}: key {url} must be https://: the key is what apt trusts")
    return url


def _dist(template: str, suite: str) -> str:
    return template.format(suite=suite, codename=suite.removeprefix("raspbian-"))


def validate(deps: list[dict]) -> list[dict]:
    """Check every entry and fill in its defaults. Raises Error."""
    out, names = [], {}
    for n, d in enumerate(deps, 1):
        where = f"[[depends]] #{n}" + (f" ({d.get('repo') or d.get('name')})"
                                       if isinstance(d.get("repo") or d.get("name"), str) else "")
        allowed = OURS if "repo" in d else EXPLICIT
        extra = sorted(set(d) - allowed)
        if extra:
            form = "`repo` form" if "repo" in d else "explicit form"
            raise Error(f"{where}: the {form} doesn't take {', '.join(extra)}"
                        + (" (a `repo` follows docs/conventions.md: its URL and key come from its site)"
                           if "repo" in d else ""))
        reason = d.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise Error(f"{where}: every entry needs a `reason`")
        suites = d.get("suites")
        if suites is not None:
            if not isinstance(suites, list) or not suites or not all(isinstance(s, str) for s in suites):
                raise Error(f"{where}: `suites` must be a non-empty list of suites (or left out: every suite)")
            unknown = [s for s in suites if s not in KNOWN_SUITES]
            if unknown:
                raise Error(f"{where}: unknown suite {', '.join(unknown)} (known: {', '.join(KNOWN_SUITES)})")
        e = {"reason": reason.strip(), "suites": suites}
        if "repo" in d:
            m = REPO.fullmatch(d["repo"]) if isinstance(d["repo"], str) else None
            if not m:
                raise Error(f"{where}: `repo` must be \"owner/name\", not {d['repo']!r}")
            e.update(repo=d["repo"], name=m.group(1))
        else:
            missing = [k for k in ("name", "url", "suite", "key") if k not in d]
            if missing:
                raise Error(f"{where}: needs `repo`, or else {', '.join(missing)}")
            name = d["name"]
            if not isinstance(name, str) or not NAME.fullmatch(name):
                raise Error(f"{where}: `name` must be letters, digits, '_', '.' and '-' "
                            f"(it names the keyring and the sources file), not {name!r}")
            url = _url_ok(d["url"], "url", where)
            suite = d["suite"]
            if not isinstance(suite, str) or not suite or re.search(r"\s", suite):
                raise Error(f"{where}: `suite` must be one word, not {suite!r}")
            for k, v in (("url", url), ("suite", suite)):
                try:
                    _dist(v, "trixie")
                except (KeyError, IndexError, ValueError):
                    raise Error(f"{where}: `{k}` {v!r} may only use {{suite}} and {{codename}}") from None
            comps = d.get("components", [])
            if not isinstance(comps, list) or not all(isinstance(c, str) and re.fullmatch(r"\S+", c)
                                                      for c in comps):
                raise Error(f"{where}: `components` must be a list of words")
            # apt's own rule: a flat repository's "suite" is a path ending in
            # "/" and has no components; a dists/ one is the reverse.
            if not comps and not suite.endswith("/"):
                raise Error(f"{where}: a flat repository (no components) needs a `suite` ending in \"/\", "
                            f"normally \"./\" with the directory in `url`, not {suite!r}")
            if comps and suite.endswith("/"):
                raise Error(f"{where}: a `suite` ending in \"/\" is a flat repository, which has no components")
            e.update(name=name, url=url.rstrip("/"), suite=suite, components=comps,
                     key=_key_url_ok(d["key"], where))
        if e["name"] in names:
            raise Error(f"{where}: name {e['name']} is also #{names[e['name']]}'s: each needs its own keyring")
        names[e["name"]] = n
        out.append(e)
    return out


def applies(entry: dict, suite: str) -> bool:
    return entry["suites"] is None or suite in entry["suites"]


# --------------------------------------------------------------------------
# The network.

def http_get(url: str, headers: dict[str, str] | None = None) -> bytes:
    """GET a URL. Raises Error with the reason. (Tests replace this.)"""
    req = urllib.request.Request(url, headers={"User-Agent": "apt-repo-action apt-sources", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise Error(f"{url}: HTTP {e.code} {e.reason}") from None
    except (urllib.error.URLError, OSError) as e:
        raise Error(f"{url}: {getattr(e, 'reason', e)}") from None


def pages_site(repo: str) -> str:
    """A repository's GitHub Pages URL, as GitHub reports it."""
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        body = http_get(f"{api}/repos/{repo}/pages", headers)
    except Error as e:
        raise Error(f"can't find {repo}'s GitHub Pages site ({e})"
                    + ("" if token else "; set GH_TOKEN: the API needs a token for this")) from None
    try:
        site = json.loads(body).get("html_url") or ""
    except (ValueError, AttributeError):
        site = ""
    if not site.startswith(("https://", "http://")):
        raise Error(f"{repo}'s GitHub Pages site has no html_url")
    return site.rstrip("/")


def resolve(entries: list[dict], suite: str) -> list[dict]:
    """The sources that apply to one suite, with every URL worked out."""
    if entries and suite not in KNOWN_SUITES:
        raise Error(f"unknown suite {suite!r} (known: {', '.join(KNOWN_SUITES)})")
    out = []
    for e in entries:
        if not applies(e, suite):
            continue
        if "repo" in e:
            site = pages_site(e["repo"])
            # docs/conventions.md: one flat repository per suite, key at the root.
            r = {"uri": f"{site}/{suite}/", "dist": "./", "components": [], "key": f"{site}/{e['name']}.gpg"}
        else:
            r = {"uri": _dist(e["url"], suite) + "/", "dist": _dist(e["suite"], suite),
                 "components": e["components"], "key": e["key"]}
        r = {"name": e["name"], "repo": e.get("repo"), "reason": e["reason"], **r,
             "keyring": f"{KEYRINGS}/{e['name']}.gpg"}
        r["line"] = " ".join(["deb", f"[signed-by={r['keyring']}]", r["uri"], r["dist"], *r["components"]])
        # How `apt-cache policy` shows the source once it is in use:
        # "<uri> ./" for a flat one, "<uri> <dist>/<component>" otherwise.
        shown = r["uri"].rstrip("/")
        r["policy"] = [f"{shown} {r['dist']}/{c}" for c in r["components"]] or [f"{shown} {r['dist']}"]
        out.append(r)
    return out


# --------------------------------------------------------------------------
# Keys.

def _crc24(data: bytes) -> int:
    crc = 0xB704CE
    for b in data:
        crc ^= b << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def dearmor(data: bytes) -> bytes:
    """An armoured public key block as a binary keyring (what `gpg --dearmor`
    does). Binary data is returned unchanged."""
    text = data.strip()
    if not text.startswith(ARMOUR_BEGIN):
        return data
    if ARMOUR_END not in text:
        raise Error("armoured key has no END line")
    inner = text[len(ARMOUR_BEGIN):text.index(ARMOUR_END)].decode("ascii", "replace")
    lines = [l.strip() for l in inner.splitlines()][1:]   # [0] is the rest of the BEGIN line
    # Armour headers (Version:, Comment:), if any, end at the first blank line.
    if "" in lines and all(":" in l for l in lines[:lines.index("")]):
        lines = lines[lines.index("") + 1:]
    body = [l for l in lines if l]
    crc = None
    if body and body[-1].startswith("=") and len(body[-1]) == 5:
        crc = body.pop()
    try:
        raw = base64.b64decode("".join(body), validate=True)
    except ValueError:
        raise Error("armoured key is not valid base64") from None
    if crc is not None and base64.b64decode(crc[1:]) != _crc24(raw).to_bytes(3, "big"):
        raise Error("armoured key fails its checksum")
    return raw


def check_public_key(raw: bytes) -> None:
    """The keyring must start with an OpenPGP public key packet (tag 6)."""
    if not raw or not raw[0] & 0x80:
        raise Error("not an OpenPGP key (an HTML page, or an error message?)")
    tag = raw[0] & 0x3F if raw[0] & 0x40 else (raw[0] >> 2) & 0x0F
    if tag != 6:
        raise Error(f"not an OpenPGP public key (first packet has tag {tag}, want 6)")


# --------------------------------------------------------------------------
# Output.

def write(resolved: list[dict], dest: Path) -> None:
    """Fetch the keys and write everything install.sh installs."""
    keys = {}
    for r in resolved:
        try:
            raw = dearmor(http_get(r["key"]))
            check_public_key(raw)
        except Error as e:
            raise Error(f"{r['name']}: can't use the key {r['key']}: {e}") from None
        keys[r["name"]] = raw
    for sub in ("keyrings", "sources.list.d"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
        for old in (dest / sub).iterdir():
            old.unlink()
    for r in resolved:
        (dest / "keyrings" / f"{r['name']}.gpg").write_bytes(keys[r["name"]])
        (dest / "sources.list.d" / f"{r['name']}.list").write_text(
            f"# {r['repo'] or r['name']}: {r['reason']}\n{r['line']}\n")
    (dest / "checks").write_text("".join(f"{r['name']} {p}\n" for r in resolved for p in r["policy"]))
    (dest / "sources.json").write_text(json.dumps(resolved, indent=1) + "\n")
    shutil.copyfile(INSTALL_SH, dest / "install.sh")
    (dest / "install.sh").chmod(0o755)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "resolve", "write"])
    ap.add_argument("--declaration", type=Path, default=Path(DECLARATION),
                    help=f"the declaration (default: {DECLARATION}); a missing file declares nothing")
    ap.add_argument("--suite", help="the suite being built or installed (resolve, write)")
    ap.add_argument("--dest", type=Path, help="the directory to write (write)")
    args = ap.parse_args()
    try:
        entries = validate(load(args.declaration))
        if args.command == "check":
            print(f"{args.declaration}: {len(entries)} dependency repositories")
            return 0
        if not args.suite:
            ap.error(f"{args.command} needs --suite")
        resolved = resolve(entries, args.suite)
        if args.command == "resolve":
            print(json.dumps(resolved, indent=1))
            return 0
        if not args.dest:
            ap.error("write needs --dest")
        write(resolved, args.dest)
        for r in resolved:
            print(f"{r['name']}: {r['line']}  (key {r['key']})", file=sys.stderr)
        print(len(resolved))
        return 0
    except Error as e:
        print(f"apt-sources.py: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
