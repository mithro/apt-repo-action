#!/usr/bin/env python3
"""Bundle the packages ours need from dependency repositories into our suites
(docs/packaging.md, "Bundling a dependency repository").

A `[[depends]]` entry with `bundle = true` (one of ours) or
`bundle = "third-party"` (anyone else's; flat only) asks publish-apt to serve
that repository's packages our packages need from our own suites, signed with
our key, so a user adds one repository instead of several.

    bundle-depends.py fetch --declaration F --suite S --debs DIR [--dest DIR] [--arch A]
    bundle-depends.py stale --declaration F --suites "S ..." --site URL

`fetch` reads our packages in --debs (what the build made for the suite),
works out which of the bundled repositories' packages they need, and copies
the newest version of each into --dest (default: --debs), with a
dpkg-scanpackages extra-override file, <dest>/.bundled-override, that marks
each copied package `Bundled-From: <repository>` in our index. Which
packages: the Depends and Pre-Depends of our packages, followed through the
bundled repositories' own packages (a package they don't have is left to
Debian). Every alternative and every virtual package a bundled repository
has counts, since it can't be known here whether Debian satisfies it: what a
dependency repository has is normally why it was declared. Architectures:
a package needed by an Architecture: all package of ours is taken for every
architecture the dependency repository has it for (ours installs anywhere);
one needed only by architecture-dependent packages, for their architectures.
--arch restricts everything to one architecture (and all): an install test.

Nothing is copied unverified. The dependency repository's InRelease must
verify with its key (gpgv), its Packages must match the hash InRelease
gives, and each .deb the Size and SHA256 its Packages gives. For one of ours
the Release must also be for this suite (Codename), so a suite can't be
served another's packages. Any failure -- an unreachable repository
included -- fails the command: publishing without the dependency would leave
our packages uninstallable for users of our repository alone.

`stale` exits 0 and prints (and writes `stale=` to $GITHUB_OUTPUT) whether a
bundled repository now has a newer version of a package our live site
bundles from it, or a bundled repository applies to a suite our site bundles
nothing from yet: then our next publish would bring something new, and
.github/workflows/refresh-bundled.yml starts it.

Needs gpgv, dpkg and dpkg-deb (a GitHub runner and any Debian image have
them). The API is read with $GH_TOKEN or $GITHUB_TOKEN.
"""
from __future__ import annotations

import argparse
import bz2
import functools
import gzip
import hashlib
import importlib.util
import lzma
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("apt_sources", Path(__file__).resolve().parent / "apt-sources.py")
apt_sources = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apt_sources)
Error = apt_sources.Error

FIELD = "Bundled-From"
OVERRIDE = ".bundled-override"
# Packages indexes in the order to prefer them, with how to read each.
INDEXES = [("Packages", lambda b: b), ("Packages.xz", lzma.decompress),
           ("Packages.gz", gzip.decompress), ("Packages.bz2", bz2.decompress)]


# --------------------------------------------------------------------------
# deb822.

def stanzas(text: str) -> list[dict[str, str]]:
    """Paragraphs of a Packages or Release file; continuation lines kept."""
    out = []
    for block in re.split(r"\n[ \t]*\n", text):
        fields: dict[str, str] = {}
        last = None
        for line in block.splitlines():
            if line[:1] in (" ", "\t") and last:
                fields[last] += "\n" + line
            elif ":" in line:
                last, _, value = line.partition(":")
                fields[last] = value.strip()
        if fields:
            out.append(fields)
    return out


def relation_names(value: str) -> list[str]:
    """Every package name a Depends-style field mentions, alternatives
    included, without versions, architecture qualifiers or restrictions."""
    names = []
    for group in value.split(","):
        for alt in group.split("|"):
            alt = re.sub(r"\([^)]*\)|\[[^]]*\]|<[^>]*>", " ", alt).split()
            if alt:
                names.append(alt[0].split(":")[0])
    return names


def compare(a: str, b: str) -> int:
    """dpkg's version ordering."""
    if a == b:
        return 0
    return -1 if subprocess.run(["dpkg", "--compare-versions", a, "lt", b]).returncode == 0 else 1


# --------------------------------------------------------------------------
# A dependency repository's verified index.

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verified_release(r: dict, suite: str) -> dict[str, tuple[str, int]]:
    """The dependency repository's InRelease, checked with its key: its
    SHA256 list, {file: (hash, size)}."""
    where = f"{r['repo'] or r['name']} ({r['uri']})"
    try:
        key = apt_sources.dearmor(apt_sources.http_get(r["key"]))
        apt_sources.check_public_key(key)
    except Error as e:
        raise Error(f"{where}: can't use its key {r['key']}: {e}") from None
    inrelease = apt_sources.http_get(r["uri"] + "InRelease")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "key.gpg").write_bytes(key)
        (Path(tmp) / "InRelease").write_bytes(inrelease)
        v = subprocess.run(["gpgv", "--keyring", f"{tmp}/key.gpg", "--output", f"{tmp}/Release",
                            f"{tmp}/InRelease"], capture_output=True, text=True)
        if v.returncode != 0:
            raise Error(f"{where}: InRelease doesn't verify with {r['key']}:\n{v.stderr.strip()}")
        # Only what gpgv vouches for: not the file as fetched.
        release = stanzas((Path(tmp) / "Release").read_text(errors="replace"))
    release = release[0] if release else {}
    if r["repo"] and release.get("Codename") != suite:
        raise Error(f"{where}: its Release is for {release.get('Codename')!r}, not {suite}")
    files = {}
    for line in release.get("SHA256", "").splitlines():
        parts = line.split()
        if len(parts) == 3:
            files[parts[2]] = (parts[0], int(parts[1]))
    return files


def verified_packages(r: dict, suite: str) -> list[dict[str, str]]:
    """The dependency repository's Packages, as its InRelease vouches for it."""
    files = verified_release(r, suite)
    for name, unpack in INDEXES:
        if name not in files:
            continue
        data = apt_sources.http_get(r["uri"] + name)
        want, size = files[name]
        if len(data) != size or sha256(data) != want:
            raise Error(f"{r['repo'] or r['name']}: {r['uri']}{name} doesn't match its InRelease")
        return [s for s in stanzas(unpack(data).decode(errors="replace")) if "Package" in s]
    raise Error(f"{r['repo'] or r['name']}: its InRelease lists no Packages index")


# --------------------------------------------------------------------------
# What ours need.

def our_packages(debs: Path, arch: str | None = None) -> list[dict[str, str]]:
    out = []
    for deb in sorted(debs.glob("*.deb")):
        text = subprocess.run(["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture",
                               "Depends", "Pre-Depends"],
                              check=True, capture_output=True, text=True).stdout
        f = stanzas(text)[0]
        if arch and f.get("Architecture") not in (arch, "all"):
            continue
        out.append(f)
    return out


def select(ours: list[dict], available: list[tuple[dict, dict]],
           arch: str | None = None) -> list[tuple[dict, dict]]:
    """The (stanza, source) pairs to bundle: for each needed (package,
    architecture), the newest version the bundled repositories have."""
    by_name: dict[str, list[tuple[dict, dict]]] = {}
    for st, src in available:
        by_name.setdefault(st["Package"], []).append((st, src))
        for prov in relation_names(st.get("Provides", "")):
            by_name.setdefault(prov, []).append((st, src))
    clash = sorted({o["Package"] for o in ours} & {st["Package"] for st, _ in available})
    if clash:
        raise Error(f"{', '.join(clash)}: both ours and a bundled repository's; one of them must be renamed")

    # name -> the architectures it is needed for; "*" is any (an all package needs it).
    needed: dict[str, set[str]] = {}
    work: list[tuple[str, set[str]]] = []

    def need(field_value: str, arches: set[str]) -> None:
        for name in relation_names(field_value):
            for st, _ in by_name.get(name, []):
                have = needed.setdefault(st["Package"], set())
                if not arches <= have:
                    have |= arches
                    work.append((st["Package"], arches))

    for o in ours:
        a = o.get("Architecture", "")
        need(o.get("Depends", "") + "," + o.get("Pre-Depends", ""), {"*"} if a == "all" else {a})
    chosen: dict[tuple[str, str], tuple[dict, dict]] = {}
    while work:
        name, arches = work.pop()
        for st, src in by_name.get(name, []):
            if st["Package"] != name:
                continue
            a = st.get("Architecture", "")
            if a != "all" and "*" not in arches and a not in arches:
                continue
            if arch and a not in (arch, "all"):
                continue
            key = (name, a)
            if key not in chosen or compare(st["Version"], chosen[key][0]["Version"]) > 0:
                chosen[key] = (st, src)
            # What it needs, for the architectures it serves.
            need(st.get("Depends", "") + "," + st.get("Pre-Depends", ""), arches if a == "all" else {a})
    return [chosen[k] for k in sorted(chosen)]


def fetch(entries: list[dict], suite: str, debs: Path, dest: Path, arch: str | None) -> list[str]:
    """Copy what ours need into dest; return the summary lines."""
    resolved = [r for r in apt_sources.resolve([e for e in entries if e["bundle"]], suite)]
    dest.mkdir(parents=True, exist_ok=True)
    override = dest / OVERRIDE
    if not resolved:
        return []
    available = []
    for r in resolved:
        available += [(st, r) for st in verified_packages(r, suite)]
    ours = our_packages(debs, arch)
    if not ours:
        raise Error(f"no packages of ours in {debs} to bundle dependencies for")
    lines, marks = [], {}
    for st, r in select(ours, available, arch):
        name = Path(st["Filename"]).name
        if "/" in name or not name.endswith(".deb"):
            raise Error(f"{r['repo'] or r['name']}: odd Filename {st['Filename']!r}")
        data = apt_sources.http_get(r["uri"] + st["Filename"].removeprefix("./"))
        if len(data) != int(st.get("Size", -1)) or sha256(data) != st.get("SHA256"):
            raise Error(f"{r['repo'] or r['name']}: {name} doesn't match its Packages index")
        out = dest / name
        if out.exists() and sha256(out.read_bytes()) != st["SHA256"]:
            raise Error(f"{name}: a different file of that name is already in {dest}")
        out.write_bytes(data)
        source = r["repo"] or r["uri"]
        marks[st["Package"]] = source
        lines.append(f"{st['Package']} {st['Version']} {st['Architecture']} from {source}")
    with override.open("a") as f:
        for pkg, source in sorted(marks.items()):
            f.write(f"{pkg} {FIELD} {source}\n")
    return lines


def stale(entries: list[dict], suites: list[str], site: str) -> list[str]:
    """Why our next publish would bundle something new; empty if nothing."""
    why = []
    for suite in suites:
        bundled = [r for r in apt_sources.resolve([e for e in entries if e["bundle"]], suite)]
        if not bundled:
            continue
        try:
            live = stanzas(apt_sources.http_get(f"{site.rstrip('/')}/{suite}/Packages").decode(errors="replace"))
        except Error as e:
            raise Error(f"can't read our own {suite}/Packages: {e}") from None
        for r in bundled:
            source = r["repo"] or r["uri"]
            ours = {}
            for st in live:
                if st.get(FIELD) == source:
                    k = (st["Package"], st.get("Architecture", ""))
                    if k not in ours or compare(st["Version"], ours[k]) > 0:
                        ours[k] = st["Version"]
            if not ours:
                why.append(f"{suite}: nothing bundled from {source} yet")
                continue
            for st in verified_packages(r, suite):
                k = (st["Package"], st.get("Architecture", ""))
                if k in ours and compare(st["Version"], ours[k]) > 0:
                    why.append(f"{suite}: {source} has {st['Package']} {st['Version']} ({k[1]}), we bundle {ours[k]}")
    return why


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["fetch", "stale"])
    ap.add_argument("--declaration", type=Path, default=Path(apt_sources.DECLARATION))
    ap.add_argument("--suite", help="fetch: the suite")
    ap.add_argument("--debs", type=Path, help="fetch: our packages for the suite")
    ap.add_argument("--dest", type=Path, help="fetch: where to put the bundled packages (default: --debs)")
    ap.add_argument("--arch", help="fetch: only this architecture (and all)")
    ap.add_argument("--suites", help="stale: space-separated suites")
    ap.add_argument("--site", help="stale: our live site")
    args = ap.parse_args()
    try:
        entries = apt_sources.validate(apt_sources.load(args.declaration))
        if args.command == "fetch":
            if not args.suite or not args.debs:
                ap.error("fetch needs --suite and --debs")
            lines = fetch(entries, args.suite, args.debs, args.dest or args.debs, args.arch)
            for line in lines:
                print(f"bundled {args.suite}: {line}")
            if not lines and any(e["bundle"] and apt_sources.applies(e, args.suite) for e in entries):
                print(f"bundled {args.suite}: nothing ours needs")
            return 0
        if not args.suites or not args.site:
            ap.error("stale needs --suites and --site")
        why = stale(entries, args.suites.split(), args.site)
        for line in why:
            print(line)
        print(f"stale: {'yes' if why else 'no'}")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"stale={'true' if why else 'false'}\n")
        return 0
    except Error as e:
        print(f"bundle-depends.py: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
