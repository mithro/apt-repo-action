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

# apt 3's solver ("librust-tiff-dev:armhf=0.11.3-2 is selected ...",
# "... Depends librust-weezl-0.1+default-dev (>= 0.1.8)") and apt 2's
# ("Depends: foo but it is not going to be installed").
NAME = re.compile(r"(?<![\w.+-])([a-z0-9][a-z0-9.+-]+)(?::[a-z0-9]+)?(?=[=\s]|$)")


def run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def newer(a: str, b: str) -> bool:
    return subprocess.run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0


def candidate(pkg: str) -> dict[str, str]:
    """The binary package's candidate stanza, or {} if apt doesn't know it."""
    text = run("apt-cache", "show", "--no-all-versions", pkg)
    return dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", text.split("\n\n")[0], re.M))


def newest_source(src: str) -> str | None:
    best = None
    for v in re.findall(r"^Version: (\S+)$", run("apt-cache", "showsrc", "--only-source", src), re.M):
        if best is None or newer(v, best):
            best = v
    return best


def main() -> None:
    names = []
    for line in sys.stdin:
        if not re.search(r"is selected|Depends|Breaks|Conflicts|not going to be installed|conflicting", line):
            continue
        for m in NAME.finditer(line):
            n = m.group(1)
            if n not in names and n not in ("builddeps", "is", "but", "none", "of", "the", "choices"):
                names.append(n)
    out = []
    for n in names:
        f = candidate(n)
        if not f.get("Version"):
            continue
        src = f.get("Source", n)
        m = re.match(r"(\S+)(?: \((\S+)\))?", src)
        name = m.group(1)
        built = m.group(2) or re.sub(r"\+b\d+$", "", f["Version"])
        latest = newest_source(name)
        if latest and newer(latest, built) and name not in out:
            print(f"stale: {n} {f['Version']} from {name} {built}; {name} {latest} is in the archive",
                  file=sys.stderr)
            out.append(name)
    print("\n".join(out))


if __name__ == "__main__":
    main()
