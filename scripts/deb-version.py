#!/usr/bin/env python3
"""The package version and changelog entry for one build (docs/packaging.md).

Implements the Set B version and the suite and preview suffixes:

    <X.Y[.Z]>[.post<N>][~deb<R>][~pr<P>]

- ``X.Y[.Z]`` is the newest ``vX.Y[.Z]`` tag reachable from HEAD, ``.post<N>``
  the commits since it (left out at the tag). With no tag, ``0.0.post<N>``
  with N counting every commit.
- ``~deb<R>`` is the suite's Debian release number; sid has none.
- ``~pr<P>`` goes last, on pull request previews only.

``--write-changelog`` prepends the build's entry to the committed
debian/changelog: ``<source> (<version>) <suite>``, "Built from
<owner>/<repo>@<sha>", the maintainer from debian/control, and the commit's
committer time, which dpkg-buildpackage then uses as SOURCE_DATE_EPOCH.

Usage (run from the source tree, with full history and tags):
    deb-version.py --suite trixie                  # print the version
    deb-version.py --suite trixie --pr 41 --write-changelog

Standard library only: it runs inside a bare debian:<suite> container.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Codename -> Debian release number, for ~deb<R>. sid has no suffix. When
# Debian makes a release, add the new testing here (docs/packaging.md,
# "Suites").
DEBIAN_RELEASE = {"bookworm": 12, "trixie": 13, "forky": 14}

# The line every generated entry carries; a committed changelog whose top
# entry has it is a build's output, committed by mistake.
BUILT_FROM = "  * Built from "


def fail(message: str) -> None:
    print(f"deb-version.py: error: {message}", file=sys.stderr)
    sys.exit(1)


def git(src: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(src), *args], capture_output=True,
                              text=True, check=True).stdout.strip()
    except FileNotFoundError:
        fail("git is not installed")
    except subprocess.CalledProcessError as e:
        fail(f"git {' '.join(args)}: {e.stderr.strip()}")


def base_version(src: Path) -> str:
    if git(src, "rev-parse", "--is-shallow-repository") == "true":
        fail("this is a shallow clone, so the commit count would be wrong and the "
             "version would go backwards. Check out with `fetch-depth: 0`.")
    r = subprocess.run(["git", "-C", str(src), "describe", "--tags", "--long",
                        "--match", "v[0-9]*"], capture_output=True, text=True)
    if r.returncode == 0:
        m = re.fullmatch(r"v(\d+\.\d+(?:\.\d+)?)-(\d+)-g[0-9a-f]+", r.stdout.strip())
        if not m:
            fail(f"tag in {r.stdout.strip()!r} is not vX.Y or vX.Y.Z")
        tag, n = m.group(1), int(m.group(2))
        return tag if n == 0 else f"{tag}.post{n}"
    return f"0.0.post{git(src, 'rev-list', '--count', 'HEAD')}"


def with_suffixes(base: str, suite: str, pr: int | None) -> str:
    """Add ~deb<R> (every suite but sid) and then ~pr<P> (previews)."""
    codename = suite.removeprefix("raspbian-")
    if codename == "sid":
        out = base
    elif codename in DEBIAN_RELEASE:
        out = f"{base}~deb{DEBIAN_RELEASE[codename]}"
    else:
        # Without a release number the build would get no suffix and sort
        # above every other suite's build of the same commit.
        fail(f"unknown suite {suite!r} (known: {', '.join([*DEBIAN_RELEASE, 'sid'])}, "
             "and raspbian-<codename>)")
    return f"{out}~pr{pr}" if pr else out


def control_field(control: str, field: str) -> str:
    """A field of debian/control's first (source) paragraph."""
    source = control.split("\n\n", 1)[0]
    m = re.search(rf"^{field}:[ \t]*(.+)$", source, re.MULTILINE | re.IGNORECASE)
    if not m:
        fail(f"debian/control has no {field}: in its source paragraph")
    return m.group(1).strip()


def changelog_entry(source: str, version: str, suite: str, repo: str, sha: str,
                    maintainer: str, date: str) -> str:
    return (f"{source} ({version}) {suite}; urgency=medium\n\n"
            f"{BUILT_FROM}{repo}@{sha}\n\n"
            f" -- {maintainer}  {date}\n")


def github_repository(src: Path) -> str:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = git(src, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        fail(f"can't tell the GitHub repository from origin {url!r}; pass --repo")
    return m.group(1)


def write_changelog(src: Path, version: str, suite: str, repo: str) -> None:
    control = (src / "debian/control").read_text()
    # The committed changelog, not the working tree's, so running this twice
    # doesn't stack two entries.
    committed = subprocess.run(["git", "-C", str(src), "show", "HEAD:debian/changelog"],
                               capture_output=True, text=True)
    old = committed.stdout if committed.returncode == 0 else ""
    top = old.split("\n -- ", 1)[0]
    if BUILT_FROM in top:
        fail("the committed debian/changelog starts with a build's generated entry. "
             "Commit the changelog without it: the build adds its own.")
    entry = changelog_entry(control_field(control, "Source"), version, suite, repo,
                            git(src, "rev-parse", "HEAD"), control_field(control, "Maintainer"),
                            git(src, "log", "-1", "--format=%cd", "--date=rfc2822"))
    (src / "debian/changelog").write_text(entry + ("\n" + old if old else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--suite", required=True,
                    help="the suite being built: bookworm, trixie, forky, sid or raspbian-<codename>")
    ap.add_argument("--pr", type=int, default=None,
                    help="pull request number, for a preview build")
    ap.add_argument("--repo", default=None,
                    help="owner/name for the changelog (default: $GITHUB_REPOSITORY, else origin)")
    ap.add_argument("--source-dir", type=Path, default=Path("."),
                    help="the source tree (default: the current directory)")
    ap.add_argument("--write-changelog", action="store_true",
                    help="prepend this build's entry to debian/changelog")
    args = ap.parse_args()
    if args.pr is not None and args.pr <= 0:
        fail(f"--pr must be a pull request number, not {args.pr}")
    version = with_suffixes(base_version(args.source_dir), args.suite, args.pr)
    if args.write_changelog:
        write_changelog(args.source_dir, version, args.suite,
                        args.repo or github_repository(args.source_dir))
    print(version)


if __name__ == "__main__":
    main()
