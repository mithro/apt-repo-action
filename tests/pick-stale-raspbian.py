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
or nothing when no Rust crate is stale and rebuildable. Candidates are tried
in order, rust-schemars-derive first (the case this was found with) while
it is stale, then by size, smallest first; the first whose Build-Depends
(with the nocheck profile, for armhf) the archive can satisfy is picked, so
the job doesn't fail because Raspbian itself can't build the crate. Rust
crates only: their -dev packages are source code, so a rebuild with nocheck
is quick. Only direct Build-Depends are checked, not what they need in turn.

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
ARCH = "armhf"
OPS = {">=": "ge", "<=": "le", ">>": "gt", "<<": "lt", "=": "eq", ">": "ge", "<": "le"}


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


def compare(a: str, op: str, b: str) -> bool:
    return subprocess.run(["dpkg", "--compare-versions", a, OPS[op], b]).returncode == 0


class Archive:
    """What the binary archives offer: every version of each package, and
    what each provides."""

    def __init__(self, packages: list[str]):
        self.have: dict[str, list[str]] = {}
        self.provides: dict[str, list[str | None]] = {}
        for text in packages:
            for f in stanzas(text):
                pkg, v = f.get("Package"), f.get("Version")
                if not pkg or not v:
                    continue
                self.have.setdefault(pkg, []).append(v)
                for p in f.get("Provides", "").replace("\n", " ").split(","):
                    m = re.match(r"\s*([^\s(]+)(?:\s*\(\s*=\s*([^\s)]+)\s*\))?", p)
                    if m and m.group(1):
                        self.provides.setdefault(m.group(1), []).append(m.group(2))

    def satisfies(self, relation: str) -> bool:
        """One alternative, e.g. "librust-foo-dev (>= 1.2) [!armel] <!nocheck>"."""
        m = re.match(r"\s*([^\s(\[<:]+)(?::\S+)?\s*(?:\(\s*(<<|<=|=|>=|>>|<|>)\s*([^\s)]+)\s*\))?", relation)
        if not m:
            return False
        name, op, want = m.groups()
        if op is None:
            return name in self.have or name in self.provides
        if any(compare(v, op, want) for v in self.have.get(name, [])):
            return True
        # A versioned relation is met only by a versioned Provides.
        return any(v is not None and compare(v, op, want) for v in self.provides.get(name, []))


def applies(alternative: str) -> bool:
    """Whether an alternative applies to an armhf build with the nocheck
    profile: its [arch] list, if any, includes armhf, and its <profile>
    restrictions, if any, hold with nocheck set."""
    arches = re.search(r"\[([^\]]*)\]", alternative)
    if arches:
        names = arches.group(1).split()
        if any(n.startswith("!") for n in names):
            if f"!{ARCH}" in names or "!any" in names:
                return False
        elif ARCH not in names and "any" not in names and "linux-any" not in names:
            return False
    formulas = re.findall(r"<([^>]*)>", alternative)
    if formulas:
        # Any formula may hold; within one, every term must.
        def holds(formula: str) -> bool:
            return all((t == "nocheck") if not t.startswith("!") else (t[1:] != "nocheck")
                       for t in formula.split())
        return any(holds(f) for f in formulas)
    return True


def buildable(build_depends: str, archive: Archive) -> list[str]:
    """The Build-Depends groups the archive can't satisfy (empty: all can)."""
    missing = []
    for group in build_depends.replace("\n", " ").split(","):
        alts = [a for a in group.split("|") if a.strip() and applies(a)]
        if not alts:
            continue  # nothing of this group applies to an armhf nocheck build
        if not any(archive.satisfies(re.sub(r"\[[^\]]*\]|<[^>]*>", "", a)) for a in alts):
            missing.append(group.strip())
    return missing


def pick(packages: list[str], sources: list[str], verbose: bool = False) -> dict[str, str] | None:
    newest: dict[str, tuple[str, int, str]] = {}
    for text in sources:
        for f in stanzas(text):
            name, v = f.get("Package"), f.get("Version")
            if not name or not v or not name.startswith("rust-"):
                continue
            size = sum(int(x.split()[1]) for x in f.get("Files", "").splitlines()[1:] if len(x.split()) == 3)
            if name not in newest or (v != newest[name][0] and newer(v, newest[name][0])):
                newest[name] = (v, size, f.get("Build-Depends", ""))
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
            stale.append((src != PREFERRED, newest[src][1], src, pkg))
    if not stale:
        return None
    archive = Archive(packages)
    for _, _, src, pkg in sorted(stale):
        missing = buildable(newest[src][2], archive)
        if not missing:
            return {"source": src, "binary": pkg, "version": newest[src][0]}
        if verbose:
            print(f"skip {src} {newest[src][0]}: can't satisfy {'; '.join(missing)}", file=sys.stderr)
    return None


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
    p = pick(packages, sources, verbose=True)
    if p is None:
        print("no stale, rebuildable Rust crate in raspbian", a.codename, file=sys.stderr)
        return
    print(f"stale: {p['binary']} from {p['source']}, whose {p['version']} isn't built for armhf", file=sys.stderr)
    for k, v in p.items():
        print(f"{k}={v}")


if __name__ == "__main__":
    main()
