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
them into --dest (default: --debs), with a
dpkg-scanpackages extra-override file, <dest>/.bundled-override, that marks
each copied package `Bundled-From: <repository>` in our index. Which
packages: for each Depends and Pre-Depends relation of ours that a bundled
repository satisfies (the package at a version the relation allows, or a
Provides), the newest satisfying version, and what that needs in turn;
several versions of one package if relations need them. A package they
don't have is left to Debian. Every satisfiable alternative counts, since
it can't be known here whether Debian satisfies it: what a dependency
repository has is normally why it was declared. A relation without
alternatives on a package a bundled repository has, none of whose versions
satisfies it, is an error. --index also writes <dest>/Packages for the
bundled files, which an install test offers apt as a local source, so apt
chooses among alternatives as a user's would. Architectures:
a package needed by an Architecture: all package of ours is taken for every
architecture the dependency repository has it for (ours installs anywhere);
one needed only by architecture-dependent packages, for their architectures.
--arch restricts everything to one architecture (and all): an install test.

Nothing is copied unverified. The dependency repository's InRelease must
verify with its key (gpgv), its Packages must match the hash InRelease
gives, and each .deb the Size and SHA256 its Packages gives, and carry in
its own control file exactly the Package, Version, Architecture and
relations its stanza gives (dpkg-scanpackages indexes the file by its
control file). For one of ours the Release must also be for this suite
(Codename), so a suite can't be served another's packages. `bundle = true`
must name a repository of the same GitHub owner as ours (--owner, or
$GITHUB_REPOSITORY's). Any failure -- an unreachable repository
included -- fails the command: publishing without the dependency would leave
our packages uninstallable for users of our repository alone.

`stale` exits 0 and prints (and writes `stale=` to $GITHUB_OUTPUT) whether
our next publish would bundle something the live site doesn't have: it runs
the same selection over our live packages and the bundled repositories'
verified indexes, and compares. Then .github/workflows/refresh-bundled.yml
starts a publish; otherwise nothing runs.

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


def relations(value: str) -> list[list[tuple[str, str | None, str | None]]]:
    """A Depends-style field as groups of alternatives, each
    (name, operator, version); architecture qualifiers, architecture lists
    and build profiles dropped. `<` and `>` are dpkg's old `<=` and `>=`."""
    groups = []
    for group in value.split(","):
        alts = []
        for alt in group.split("|"):
            alt = re.sub(r"\[[^]]*\]|<[^>]*>", " ", alt)
            m = re.fullmatch(r"\s*([^\s(:]+)(?::\S+)?\s*(?:\(\s*(<<|<=|=|>=|>>|<|>)\s*([^\s)]+)\s*\))?\s*", alt)
            if m:
                op = {"<": "<=", ">": ">="}.get(m.group(2), m.group(2))
                alts.append((m.group(1), op, m.group(3)))
        if alts:
            groups.append(alts)
    return groups


def relation_names(value: str) -> list[str]:
    """Every package name a Depends-style field mentions, alternatives
    included, without versions, architecture qualifiers or restrictions."""
    return [name for group in relations(value) for name, _, _ in group]


def compare(a: str, b: str) -> int:
    """dpkg's version ordering."""
    if a == b:
        return 0
    return -1 if subprocess.run(["dpkg", "--compare-versions", a, "lt", b]).returncode == 0 else 1


def satisfies(version: str, op: str | None, want: str | None) -> bool:
    """Whether `version` satisfies `(op want)`, by dpkg's own comparison."""
    if op is None:
        return True
    return subprocess.run(["dpkg", "--compare-versions", version, op, want]).returncode == 0


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


def provides(st: dict, name: str, op: str | None, want: str | None) -> bool:
    """Whether a package's Provides satisfies (name op want): an unversioned
    relation by any Provides of the name, a versioned one only by a
    versioned `name (= v)` that satisfies it (dpkg's rule)."""
    for group in relations(st.get("Provides", "")):
        for pname, pop, pver in group:
            if pname != name:
                continue
            if op is None or (pop == "=" and satisfies(pver, op, want)):
                return True
    return False


def select(ours: list[dict], available: list[tuple[dict, dict]],
           arch: str | None = None) -> list[tuple[dict, dict]]:
    """The (stanza, source) pairs to bundle.

    Each relation of ours (Depends, Pre-Depends) that a bundled repository
    can satisfy -- by the package itself at a version the relation allows,
    or by a Provides -- takes, for each architecture it is needed for, the
    newest version that satisfies it; and those in turn what they need.
    Several versions of one package are bundled if different relations need
    them. A relation with no alternatives naming a package the bundled
    repositories have, none of whose versions satisfies it, is an error:
    ours would be uninstallable from our repository alone."""
    real: dict[str, list[tuple[dict, dict]]] = {}
    virtual: dict[str, list[tuple[dict, dict]]] = {}
    for st, src in available:
        real.setdefault(st["Package"], []).append((st, src))
        for prov in relation_names(st.get("Provides", "")):
            virtual.setdefault(prov, []).append((st, src))
    clash = sorted({o["Package"] for o in ours} & set(real))
    if clash:
        raise Error(f"{', '.join(clash)}: both ours and a bundled repository's; one of them must be renamed")

    def fits_arch(st: dict, arches: set[str]) -> bool:
        a = st.get("Architecture", "")
        if arch and a not in (arch, "all"):
            return False
        return a == "all" or "*" in arches or a in arches

    chosen: dict[tuple[str, str, str], tuple[dict, dict]] = {}
    done: set[tuple[str, frozenset]] = set()
    work: list[tuple[str, str, set[str]]] = []   # (relations, whose, arches)
    for o in ours:
        a = o.get("Architecture", "")
        work.append((o.get("Depends", "") + "," + o.get("Pre-Depends", ""), o["Package"],
                     {"*"} if a == "all" else {a}))
    while work:
        field, whose, arches = work.pop()
        if (field, frozenset(arches)) in done:
            continue
        done.add((field, frozenset(arches)))
        for group in relations(field):
            found = False
            for name, op, want in group:
                cands = [(st, src) for st, src in real.get(name, [])
                         if satisfies(st["Version"], op, want) and fits_arch(st, arches)]
                cands += [(st, src) for st, src in virtual.get(name, [])
                          if st["Package"] != name and provides(st, name, op, want) and fits_arch(st, arches)]
                # The newest satisfying version per (package, architecture).
                best: dict[tuple[str, str], tuple[dict, dict]] = {}
                for st, src in cands:
                    k = (st["Package"], st.get("Architecture", ""))
                    if k not in best or compare(st["Version"], best[k][0]["Version"]) > 0:
                        best[k] = (st, src)
                for (pkg, a), (st, src) in best.items():
                    found = True
                    key = (pkg, st["Version"], a)
                    if key not in chosen:
                        chosen[key] = (st, src)
                    work.append((st.get("Depends", "") + "," + st.get("Pre-Depends", ""), pkg,
                                 arches if a == "all" else {a}))
            if not found and len(group) == 1 and group[0][0] in real:
                name, op, want = group[0]
                have = sorted({st["Version"] for st, _ in real[name]}, key=functools.cmp_to_key(compare))
                raise Error(f"{whose} needs {name}" + (f" ({op} {want})" if op else "")
                            + f", and the bundled repository has {name} only at {', '.join(have)}"
                            + (f" for the architectures needed" if not op else ""))
    return [chosen[k] for k in sorted(chosen)]


# The control fields a bundled .deb must carry exactly as its verified
# stanza says: dpkg-scanpackages indexes a package by its own control file,
# so a file whose hash matches but whose control names another package (one
# of ours, say, at a higher version) would otherwise enter our signed index
# unmarked.
CONTROL = ["Package", "Version", "Architecture", "Source", "Multi-Arch", "Essential",
           "Depends", "Pre-Depends", "Provides", "Conflicts", "Breaks", "Replaces"]


def control_matches(path: Path, st: dict) -> list[str]:
    """The CONTROL fields where the .deb's control file and the stanza differ."""
    r = subprocess.run(["dpkg-deb", "-f", str(path), *CONTROL], capture_output=True, text=True)
    if r.returncode != 0:
        return [f"not a Debian package ({r.stderr.strip()})"]
    text = r.stdout
    control = stanzas(text)[0] if text.strip() else {}
    norm = lambda v: " ".join((v or "").split())   # noqa: E731
    return [f"{k}: {control.get(k)!r} in the file, {st.get(k)!r} in its index"
            for k in CONTROL if norm(control.get(k)) != norm(st.get(k))]


def owner_of(args_owner: str | None) -> str:
    owner = args_owner or os.environ.get("GITHUB_REPOSITORY", "").partition("/")[0]
    if not owner:
        raise Error("whose repository is this? pass --owner, or set $GITHUB_REPOSITORY: "
                    "`bundle = true` is only for the same owner's repositories")
    return owner


def fetch(entries: list[dict], suite: str, debs: Path, dest: Path, arch: str | None,
          index: bool = False) -> list[str]:
    """Copy what ours need into dest; return the summary lines. With
    `index`, also write dest/Packages for the bundled files alone, so an
    install test can offer them to apt as a local source."""
    resolved = [r for r in apt_sources.resolve([e for e in entries if e["bundle"]], suite)]
    dest.mkdir(parents=True, exist_ok=True)
    override = dest / OVERRIDE
    if index:
        (dest / "Packages").write_text("")
    if not resolved:
        return []
    available = []
    for r in resolved:
        available += [(st, r) for st in verified_packages(r, suite)]
    ours = our_packages(debs, arch)
    if not ours:
        raise Error(f"no packages of ours in {debs} to bundle dependencies for")
    lines, marks, stanza_text = [], {}, []
    for st, r in select(ours, available, arch):
        where = r["repo"] or r["name"]
        name = Path(st["Filename"]).name
        if "/" in name or not name.endswith(".deb"):
            raise Error(f"{where}: odd Filename {st['Filename']!r}")
        data = apt_sources.http_get(r["uri"] + st["Filename"].removeprefix("./"))
        if len(data) != int(st.get("Size", -1)) or sha256(data) != st.get("SHA256"):
            raise Error(f"{where}: {name} doesn't match its Packages index")
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / name
            probe.write_bytes(data)
            wrong = control_matches(probe, st)
        if wrong:
            raise Error(f"{where}: {name}'s control file isn't what its index says: " + "; ".join(wrong))
        out = dest / name
        if out.exists() and sha256(out.read_bytes()) != st["SHA256"]:
            raise Error(f"{name}: a different file of that name is already in {dest}")
        out.write_bytes(data)
        source = r["repo"] or r["uri"]
        marks[st["Package"]] = source
        lines.append(f"{st['Package']} {st['Version']} {st['Architecture']} from {source}")
        stanza_text.append("".join(f"{k}: {v}\n" for k, v in
                                   {**st, "Filename": f"./{name}", FIELD: source}.items()))
    with override.open("a") as f:
        for pkg, source in sorted(marks.items()):
            f.write(f"{pkg} {FIELD} {source}\n")
    if index:
        (dest / "Packages").write_text("\n".join(stanza_text))
    return lines


def stale(entries: list[dict], suites: list[str], site: str) -> list[str]:
    """Why our next publish would bundle something the live site doesn't:
    what select() would choose for our live packages, less what the live
    site already bundles from that repository. Empty if nothing."""
    why = []
    for suite in suites:
        bundled = [r for r in apt_sources.resolve([e for e in entries if e["bundle"]], suite)]
        if not bundled:
            continue
        try:
            live = stanzas(apt_sources.http_get(f"{site.rstrip('/')}/{suite}/Packages").decode(errors="replace"))
        except Error as e:
            raise Error(f"can't read our own {suite}/Packages: {e}") from None
        live = [st for st in live if "Package" in st]
        # Ours: the newest of each (package, architecture) we publish.
        newest: dict[tuple[str, str], dict] = {}
        for st in live:
            if st.get(FIELD):
                continue
            k = (st["Package"], st.get("Architecture", ""))
            if k not in newest or compare(st["Version"], newest[k]["Version"]) > 0:
                newest[k] = st
        have = {(st["Package"], st["Version"], st.get("Architecture", ""), st.get(FIELD))
                for st in live if st.get(FIELD)}
        available = [(st, r) for r in bundled for st in verified_packages(r, suite)]
        for st, r in select(list(newest.values()), available):
            source = r["repo"] or r["uri"]
            if (st["Package"], st["Version"], st.get("Architecture", ""), source) not in have:
                why.append(f"{suite}: would bundle {st['Package']} {st['Version']} "
                           f"({st.get('Architecture', '')}) from {source}, which the site doesn't have")
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
    ap.add_argument("--index", action="store_true",
                    help="fetch: also write <dest>/Packages for the bundled files, for an install test")
    ap.add_argument("--owner", help="the GitHub owner of the repository declaring them "
                                    "(default: from $GITHUB_REPOSITORY)")
    ap.add_argument("--suites", help="stale: space-separated suites")
    ap.add_argument("--site", help="stale: our live site")
    args = ap.parse_args()
    try:
        deps = apt_sources.load(args.declaration)
        # `bundle = true` is for the same owner's repositories only.
        owner = owner_of(args.owner) if any(d.get("bundle") is True for d in deps) else None
        entries = apt_sources.validate(deps, owner=owner)
        if args.command == "fetch":
            if not args.suite or not args.debs:
                ap.error("fetch needs --suite and --debs")
            lines = fetch(entries, args.suite, args.debs, args.dest or args.debs, args.arch, args.index)
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
