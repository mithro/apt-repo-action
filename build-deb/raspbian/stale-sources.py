#!/usr/bin/env python3
"""Which source packages to rebuild, from a failed `apt-get build-dep`.

Reads apt's error output on stdin and prints, one per line, the source
packages that (a) build a binary package apt's message names and (b) have a
newer version in the configured deb-src archives than the one that binary was
built from: Raspbian's builders haven't rebuilt them yet, and the old binary
is what stops the build dependencies installing (build-deb/raspbian/
build-dep.sh). Runs inside the Raspbian build container, where apt knows the
suite, <codename>-staging and their Sources. Standard library only.
"""
import re
import subprocess
import sys
from collections.abc import Callable, Iterable

# apt 3's solver ("librust-tiff-dev:armhf=0.11.3-2 is selected ...",
# "... Depends librust-weezl-0.1+default-dev (>= 0.1.8)") and apt 2's
# ("Depends: foo but it is not going to be installed").
NAME = re.compile(r"(?<![\w.+-])([a-z0-9][a-z0-9.+-]+)(?::[a-z0-9]+)?(?=[=\s]|$)")
RELEVANT = re.compile(r"is selected|is not selected|Depends|Breaks|Conflicts|not going to be installed|conflicting")
# Words of apt's own messages that look like package names; anything else
# apt doesn't know as a package is dropped by the lookup anyway.
WORDS = {"builddeps", "is", "not", "but", "none", "of", "the", "choices", "to", "for", "install",
         "selected", "because", "as", "above", "two", "conflicting", "satisfy", "dependencies",
         "reached", "assignments", "going", "be", "installed", "depends", "breaks", "conflicts"}


def names_from(lines: Iterable[str]) -> list[str]:
    """The package-like names on apt's dependency-problem lines, in order."""
    names: list[str] = []
    for line in lines:
        if not RELEVANT.search(line):
            continue
        for m in NAME.finditer(line):
            n = m.group(1)
            if n in WORDS or not re.search(r"[a-z]", n) or n in names:
                continue
            names.append(n)
    return names


def stale_sources(names: Iterable[str], candidate: Callable[[str], dict[str, str]],
                  newest_source: Callable[[str], str | None],
                  newer: Callable[[str, str], bool]) -> list[tuple[str, str, str, str, str]]:
    """(source, binary, binary version, built-from version, newest source
    version) for each named binary whose source is newer in the archive."""
    out: list[tuple[str, str, str, str, str]] = []
    seen: set[str] = set()
    for n in names:
        f = candidate(n)
        if not f.get("Version"):
            continue  # not a (real) package apt knows: a word, a virtual name
        m = re.match(r"(\S+)(?: \((\S+)\))?", f.get("Source", n))
        name = m.group(1)
        built = m.group(2) or re.sub(r"\+b\d+$", "", f["Version"])
        if name in seen:
            continue
        latest = newest_source(name)
        if latest and newer(latest, built):
            seen.add(name)
            out.append((name, n, f["Version"], built, latest))
    return out


def run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def dpkg_newer(a: str, b: str) -> bool:
    return subprocess.run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0


def apt_candidate(pkg: str) -> dict[str, str]:
    """The binary package's candidate stanza, or {} if apt doesn't know it."""
    text = run("apt-cache", "show", "--no-all-versions", pkg)
    return dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", text.split("\n\n")[0], re.M))


def apt_newest_source(src: str) -> str | None:
    best = None
    for v in re.findall(r"^Version: (\S+)$", run("apt-cache", "showsrc", "--only-source", src), re.M):
        if best is None or dpkg_newer(v, best):
            best = v
    return best


def main() -> None:
    for name, binary, version, built, latest in stale_sources(
            names_from(sys.stdin), apt_candidate, apt_newest_source, dpkg_newer):
        print(f"stale: {binary} {version} from {name} {built}; {name} {latest} is in the archive",
              file=sys.stderr)
        print(name)


if __name__ == "__main__":
    main()
