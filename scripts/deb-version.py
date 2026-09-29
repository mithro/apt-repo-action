#!/usr/bin/env python3
"""The package version and changelog entry for one build (docs/packaging.md).

Implements the Set B version, its patch series form, and the suite and
preview suffixes:

    [<E>:]<X.Y[.Z]>[.post<N>][~deb<R>][~pr<P>]                   Set B
    [<E>:]<upstream>+<owner-tag>.<X.Y[.Z]>[.post<N>][~deb<R>][~pr<P>]   patch series

- ``X.Y[.Z]`` is the newest ``vX.Y[.Z]`` tag reachable from HEAD, ``.post<N>``
  the commits since it (left out at the tag). With no tag, ``0.0.post<N>``
  with N counting every commit.
- ``<upstream>`` (patch series only) is the fetched project's version:
  given (``--upstream-version``), or its own ``git describe --tags`` at the
  pinned commit (``--upstream-dir``): ``1.1.1`` at its tag ``v1.1.1``,
  ``1.1.1.post173`` 173 commits later. ``<owner-tag>`` is the owner's tag
  (``fpgasonline``, ``welland``).
- ``<E>:`` only with ``--epoch``, which a repository uses only under a
  declared PKG-VERSION exception (rpi-qemu's epoch 2).
- ``~deb<R>`` is the suite's Debian release number; sid has none.
- ``~pr<P>`` goes last, on pull request previews only.

Without ``--write-changelog`` nothing but git is read, so a repository with
no debian/ (a Go program packaged with nfpm) gets its version this way too.

``--write-changelog`` writes debian/changelog with the build's entry:
``<source> (<version>) <suite>``, "Built from <owner>/<repo>@<sha>", the
maintainer from debian/control, and the commit's committer time, which
dpkg-buildpackage then uses as SOURCE_DATE_EPOCH. A Set A repository's
committed changelog stays underneath it; a Set B repository commits none, so
the entry is the whole file.

Usage (run from the source tree, with full history and tags):
    deb-version.py --suite trixie                  # print the version
    deb-version.py --suite trixie --pr 41 --write-changelog
    deb-version.py --suite trixie --owner-tag fpgasonline --upstream-dir build/src/openocd
    deb-version.py --suite trixie --owner-tag fpgasonline --upstream-version 20260914 \
        --source-dir build/src/libpio --version-tree . --write-changelog

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
    # Only vX.Y[.Z] are releases: the exclude drops any tag with something
    # other than digits and dots after the v, such as the v0.1.112.g91e6fbe
    # tags rpi-qemu's old release job made for every build.
    r = subprocess.run(["git", "-C", str(src), "describe", "--tags", "--long",
                        "--match", "v[0-9]*", "--exclude", "v*[!0-9.]*"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        m = re.fullmatch(r"v(\d+\.\d+(?:\.\d+)?)-(\d+)-g[0-9a-f]+", r.stdout.strip())
        if not m:
            fail(f"tag in {r.stdout.strip()!r} is not vX.Y or vX.Y.Z")
        tag, n = m.group(1), int(m.group(2))
        return tag if n == 0 else f"{tag}.post{n}"
    return f"0.0.post{git(src, 'rev-list', '--count', 'HEAD')}"


def upstream_from_describe(describe: str) -> str:
    """An upstream ``git describe --tags --long`` as a Debian upstream version.

    ``v1.1.1-173-g24e46d1`` is ``1.1.1.post173``, ``v11.1.0-0-g...`` is
    ``11.1.0``. A leading v goes; ``-`` in the tag becomes ``~``, so a
    release candidate sorts below its release (``v11.0.0-rc2`` is
    ``11.0.0~rc2``); ``_`` becomes ``.``.
    """
    m = re.fullmatch(r"(.+)-(\d+)-g[0-9a-f]+", describe)
    if not m:
        fail(f"can't read upstream's git describe {describe!r}")
    tag, n = m.group(1), int(m.group(2))
    v = re.sub(r"^[vV](?=\d)", "", tag).replace("_", ".").replace("-", "~")
    check_upstream(v, f"upstream's tag {tag!r}", " Pass --upstream-version instead.")
    return v if n == 0 else f"{v}.post{n}"


def check_upstream(v: str, what: str, hint: str = "") -> None:
    # No '-' (it would start a Debian revision) and no ':' (an epoch).
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~]*", v):
        fail(f"{what} gives {v!r}, which is not a Debian upstream version (a digit, then "
             f"letters, digits and . + ~).{hint}")


def upstream_version(up: Path) -> str:
    """The fetched project's version, from its own tags at the pinned commit."""
    shallow = git(up, "rev-parse", "--is-shallow-repository") == "true"
    r = subprocess.run(["git", "-C", str(up), "describe", "--tags", "--long"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"upstream tree {up}: git describe --tags found no tag"
             + (" (it is a shallow clone: fetch the pinned tag, or more history)" if shallow
                else "") + ". Pass --upstream-version instead.")
    describe = r.stdout.strip()
    # A shallow clone at the tag itself (count 0) describes exactly; past it
    # the count stops at the clone's depth.
    if shallow and describe.rsplit("-", 2)[1] != "0":
        fail(f"upstream tree {up} is a shallow clone past its tag, so the commit count "
             "would be wrong. Clone at the tag, or with the history back to it.")
    return upstream_from_describe(describe)


def package_version(base: str, upstream: str | None, owner_tag: str | None,
                    epoch: int | None) -> str:
    """Set B's version, or the patch series form around it; then the epoch."""
    version = base
    if upstream is not None:
        check_upstream(upstream, "--upstream-version")
        if not owner_tag or not re.fullmatch(r"[a-z]+", owner_tag):
            fail(f"--owner-tag must be lower-case letters (fpgasonline, welland), not {owner_tag!r}")
        version = f"{upstream}+{owner_tag}.{base}"
    if epoch is not None:
        if epoch < 1:
            fail(f"--epoch must be 1 or more, not {epoch}")
        version = f"{epoch}:{version}"
    return version


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


def write_changelog(src: Path, tree: Path, version: str, suite: str, repo: str) -> None:
    """The entry goes in src's debian/changelog; the commit it names is tree's."""
    if not (src / "debian/control").is_file():
        fail(f"{src / 'debian/control'} does not exist, so there is no changelog to write")
    control = (src / "debian/control").read_text()
    # The committed changelog, not the working tree's, so running this twice
    # doesn't stack two entries. Set B commits none: the entry is the file.
    # (HEAD:./ is relative to src, which may be below its repository's root.)
    committed = subprocess.run(["git", "-C", str(src), "show", "HEAD:./debian/changelog"],
                               capture_output=True, text=True)
    old = committed.stdout if committed.returncode == 0 else ""
    top = old.split("\n -- ", 1)[0]
    if BUILT_FROM in top:
        fail("the committed debian/changelog starts with a build's generated entry. "
             "Commit the changelog without it: the build adds its own.")
    entry = changelog_entry(control_field(control, "Source"), version, suite, repo,
                            git(tree, "rev-parse", "HEAD"), control_field(control, "Maintainer"),
                            git(tree, "log", "-1", "--format=%cd", "--date=rfc2822"))
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
                    help="the source tree, whose debian/ gets the changelog (default: the "
                         "current directory)")
    ap.add_argument("--version-tree", type=Path, default=None,
                    help="the git checkout of this repository whose tags and commits give "
                         "X.Y.postN and the changelog's commit (default: --source-dir). A patch "
                         "series builds in the fetched project's tree, so it passes its own here.")
    up = ap.add_mutually_exclusive_group()
    up.add_argument("--upstream-version", default=None,
                    help="patch series: the fetched project's version, as its pin records it")
    up.add_argument("--upstream-dir", type=Path, default=None,
                    help="patch series: the fetched project's checkout, at the pinned commit; "
                         "its git describe --tags gives the version")
    ap.add_argument("--owner-tag", default=None,
                    help="patch series: the owner's tag between the two versions (fpgasonline)")
    ap.add_argument("--epoch", type=int, default=None,
                    help="an epoch, only under a declared PKG-VERSION exception")
    ap.add_argument("--write-changelog", action="store_true",
                    help="write this build's entry to debian/changelog, above the committed one if any")
    args = ap.parse_args()
    if args.pr is not None and args.pr <= 0:
        fail(f"--pr must be a pull request number, not {args.pr}")
    patch_series = args.upstream_version is not None or args.upstream_dir is not None
    if patch_series != (args.owner_tag is not None):
        fail("a patch series needs --owner-tag and one of --upstream-version or --upstream-dir")
    tree = args.version_tree or args.source_dir
    upstream = (upstream_version(args.upstream_dir) if args.upstream_dir is not None
                else args.upstream_version)
    version = with_suffixes(package_version(base_version(tree), upstream, args.owner_tag,
                                            args.epoch), args.suite, args.pr)
    if args.write_changelog:
        write_changelog(args.source_dir, tree, version, args.suite,
                        args.repo or github_repository(tree))
    print(version)


if __name__ == "__main__":
    main()
