#!/usr/bin/env python3
"""Pick a Raspbian source package whose armhf binary is older than the
source, for the self-test of build-deb's automatic rebuild
(build-deb-raspbian-staging, rebuild cases).

Which packages are stale changes as Raspbian's builders catch up, so the
self-test asks the live archive each time rather than naming one. Reads
<codename> and <codename>-staging (armhf Packages and Sources) and prints
GitHub step outputs:
  source=<source package>  binary=<a librust-*-dev it builds>  version=<the
  newest source version, which the binary doesn't have yet>
or nothing when no Rust crate is stale. Prefers rust-schemars-derive (the
case this was found with) while it is stale, then the smallest source.
Rust crates only: their -dev packages are source code, so a rebuild with
nocheck is quick.

    pick-stale-raspbian.py <codename> [--packages F --sources F ...]

Standard library, plus dpkg --compare-versions.
"""
import argparse
import lzma
import re
import subprocess
import sys
import urllib.request

ARCHIVE = "http://archive.raspbian.org/raspbian/dists"
PREFERRED = "rust-schemars-derive"


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=120) as r:
        return lzma.decompress(r.read()).decode()


def stanzas(text: str):
    for s in text.split("\n\n"):
        f = {}
        key = None
        for line in s.splitlines():
            if line[:1] in (" ", "\t") and key:
                f[key] += "\n" + line
            elif ":" in line:
                key, _, v = line.partition(":")
                f[key] = v.strip()
        if f:
            yield f


def newer(a: str, b: str) -> bool:
    return subprocess.run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0


def pick(packages: list[str], sources: list[str]) -> dict[str, str] | None:
    newest: dict[str, tuple[str, int]] = {}
    for text in sources:
        for f in stanzas(text):
            name, v = f.get("Package"), f.get("Version")
            if not name or not v or not name.startswith("rust-"):
                continue
            size = sum(int(x.split()[1]) for x in f.get("Files", "").splitlines()[1:] if len(x.split()) == 3)
            if name not in newest or (v != newest[name][0] and newer(v, newest[name][0])):
                newest[name] = (v, size)
    # The newest binary of each package, and what it was built from.
    built: dict[str, tuple[str, str, str]] = {}
    for text in packages:
        for f in stanzas(text):
            pkg, v = f.get("Package"), f.get("Version")
            if (not pkg or not v or f.get("Architecture") == "all"
                    or not (pkg.startswith("librust-") and pkg.endswith("-dev"))):
                continue
            m = re.match(r"(\S+)(?: \((\S+)\))?", f.get("Source", pkg))
            src, from_v = m.group(1), m.group(2) or re.sub(r"\+b\d+$", "", v)
            if pkg not in built or (v != built[pkg][2] and newer(v, built[pkg][2])):
                built[pkg] = (src, from_v, v)
    stale = []
    for pkg, (src, from_v, _) in built.items():
        if not src.startswith("rust-"):
            continue
        if src in newest and newest[src][0] != from_v and newer(newest[src][0], from_v):
            stale.append((src != PREFERRED, newest[src][1], src, pkg, newest[src][0]))
    if not stale:
        return None
    _, _, src, pkg, v = min(stale)
    return {"source": src, "binary": pkg, "version": v}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("codename")
    ap.add_argument("--packages", action="append", help="local Packages files instead of the archive's")
    ap.add_argument("--sources", action="append", help="local Sources files instead of the archive's")
    a = ap.parse_args()
    if a.packages or a.sources:
        packages = [open(p).read() for p in a.packages or []]
        sources = [open(p).read() for p in a.sources or []]
    else:
        dists = (a.codename, f"{a.codename}-staging")
        packages = [fetch(f"{ARCHIVE}/{d}/main/binary-armhf/Packages.xz") for d in dists]
        sources = [fetch(f"{ARCHIVE}/{d}/main/source/Sources.xz") for d in dists]
    p = pick(packages, sources)
    if p is None:
        print("no stale Rust crate in raspbian", a.codename, file=sys.stderr)
        return
    print(f"stale: {p['binary']} from {p['source']}, whose {p['version']} isn't built for armhf", file=sys.stderr)
    for k, v in p.items():
        print(f"{k}={v}")


if __name__ == "__main__":
    main()
