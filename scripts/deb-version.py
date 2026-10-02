#!/usr/bin/env python3
"""The package version and changelog entry for one build (docs/packaging.md).

Implements the Set B version, its patch series form, the Set A version, and
the suite and preview suffixes:

    [<E>:]<X.Y[.Z]>[.post<N>][~deb<R>][~pr<P>]                   Set B
    [<E>:]<upstream>+<owner-tag>.<X.Y[.Z]>[.post<N>][~deb<R>][~pr<P>]   patch series
    [<E>:]<base>+<owner-tag><M>[~deb<R>][~pr<P>]                 Set A

- ``X.Y[.Z]`` is the newest ``vX.Y[.Z]`` tag reachable from HEAD, ``.post<N>``
  the commits since it (left out at the tag). With no tag, ``0.0.post<N>``
  with N counting every commit.
- ``<upstream>`` (patch series only) is the fetched project's version:
  given (``--upstream-version``), or its own ``git describe --tags`` at the
  pinned commit (``--upstream-dir``): ``1.1.1`` at its tag ``v1.1.1``,
  ``1.1.1.post173`` 173 commits later. ``<owner-tag>`` is the owner's tag
  (``fpgasonline``, ``welland``). When the fetched project is a Debian source
  package (``--upstream-debian-version``), ``<upstream>`` is its own version,
  revision included (``43.0.0-3+deb13u1``), and the fetched
  debian/changelog stays under the build's entry.
- Set A (``--upstream-branch upstream --owner-tag welland``): the repository
  is upstream's history (the ``upstream`` branch) plus ours. The upstream
  commit built is the merge base of HEAD and that branch; ``<M>`` counts the
  commits of HEAD that aren't on it. ``<base>`` is, in this order:
  1. Debian's version (``1.1.2-7``), when the committed debian/changelog is
     Debian's for the very release the upstream commit is;
  2. ``<release>-0`` (``2.93-0``) when the upstream commit is a release;
  3. ``<release>+git<N>.g<sha7>-0`` N upstream commits after one;
  4. ``0.0+git<N>.g<sha7>-0``, N counting every upstream commit, when
     upstream has no tags.
  The release is the nearest tag (``git describe --tags``, or only those
  matching ``--upstream-tag-match``), normalised: a leading v or
  project-name prefix goes, ``_`` becomes ``.`` (``v2.93``,
  ``netplan-1.1.2``, ``RELEASE_7_5``), and a pre-release gets a ``~``, so it
  sorts below its release (``3.8-rc3`` is ``3.8~rc3``, ``2.94rc1`` is
  ``2.94~rc1``). An upstream whose release tags aren't in the built branch's
  history (smartmontools: imported from svn) names its releases by commit
  subject instead: ``--upstream-release-subject``, or ``[version]
  release-subject`` in the declaration; the release is then the nearest
  such commit on the branch's own line (its first parents).
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
    deb-version.py --suite trixie --owner-tag welland --upstream-branch upstream

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


def warn(message: str) -> None:
    """To stderr: stdout is the version. Under GitHub Actions also as a
    warning annotation, which the runner reads from either stream."""
    print(f"deb-version.py: warning: {message}", file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=deb-version.py::{message}", file=sys.stderr)


def git(src: Path, *args: str) -> str:
    try:
        # errors=replace: an old commit's subject needn't be UTF-8 (an svn import).
        return subprocess.run(["git", "-C", str(src), *args], capture_output=True,
                              text=True, errors="replace", check=True).stdout.strip()
    except FileNotFoundError:
        fail("git is not installed")
    except subprocess.CalledProcessError as e:
        fail(f"git {' '.join(args)}: {e.stderr.strip()}")


def check_not_shallow(src: Path) -> None:
    if git(src, "rev-parse", "--is-shallow-repository") == "true":
        fail("this is a shallow clone, so the commit count would be wrong and the "
             "version would go backwards. Check out with `fetch-depth: 0`.")


def base_version(src: Path) -> str:
    check_not_shallow(src)
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


# A leading v, or a project-name prefix, which may itself hold - or _
# (netplan-1.1.2, RELEASE_7_5, my-project-1.2): up to the first - or _ that
# a digit follows.
TAG_PREFIX = r"^(?:[vV]|[A-Za-z][A-Za-z0-9.+_-]*?[-_])(?=\d)"
# A pre-release word straight after a digit, or after a dot (2.94rc1, 1.0.rc1).
PRERELEASE = re.compile(r"(?<=\d)\.?(?=(?:alpha|beta|pre|rc|test))", re.IGNORECASE)


def parse_describe(describe: str) -> tuple[str, int]:
    """``git describe --tags --long``'s tag and the commits since it."""
    m = re.fullmatch(r"(.+)-(\d+)-g[0-9a-f]+", describe)
    if not m:
        fail(f"can't read upstream's git describe {describe!r}")
    return m.group(1), int(m.group(2))


def tag_version(tag: str, what: str, hint: str = "", strip_prefix: bool = False,
                release: bool = False) -> str:
    """An upstream tag as a Debian upstream version.

    A leading v goes, and with `strip_prefix` a project-name prefix too
    (``netplan-1.1.2`` is ``1.1.2``); ``_`` becomes ``.``; ``-`` becomes
    ``~``, so a release candidate sorts below its release (``v11.0.0-rc2``
    is ``11.0.0~rc2``).

    With `release` (Set A, whose version must also sort against Debian's),
    a pre-release word written without the hyphen gets the ``~`` as well
    (``2.94rc1`` is ``2.94~rc1``, ``2.94test1`` is ``2.94~test1``), and a
    hyphen before a digit is refused: ``release-2024-01-15`` or ``1.2-3``
    would become ``2024~01~15`` or ``1.2~3``, which sort below ``2024`` and
    ``1.2``."""
    v = re.sub(TAG_PREFIX if strip_prefix else r"^[vV](?=\d)", "", tag).replace("_", ".")
    if release:
        if re.search(r"-\d", v):
            fail(f"{what} has a hyphen before a digit ({v!r}), which can't be told from a "
                 f"pre-release's and would sort below the version before it.{hint}")
        v = PRERELEASE.sub("~", v)
    v = v.replace("-", "~")
    check_upstream(v, what, hint)
    return v


def upstream_from_describe(describe: str, strip_prefix: bool = False) -> str:
    """An upstream ``git describe --tags --long`` as a Debian upstream version.

    ``v1.1.1-173-g24e46d1`` is ``1.1.1.post173``, ``v11.1.0-0-g...`` is
    ``11.1.0``. A leading v goes; ``-`` in the tag becomes ``~``, so a
    release candidate sorts below its release (``v11.0.0-rc2`` is
    ``11.0.0~rc2``); ``_`` becomes ``.``. With `strip_prefix` (a tag
    chosen by --upstream-tag-match), a project-name prefix goes too:
    ``netplan-1.1.2`` is ``1.1.2``.
    """
    tag, n = parse_describe(describe)
    v = tag_version(tag, f"upstream's tag {tag!r}", " Pass --upstream-version instead.",
                    strip_prefix)
    return v if n == 0 else f"{v}.post{n}"


def check_upstream(v: str, what: str, hint: str = "") -> None:
    # No '-' (it would start a Debian revision) and no ':' (an epoch).
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~]*", v):
        fail(f"{what} gives {v!r}, which is not a Debian upstream version (a digit, then "
             f"letters, digits and . + ~).{hint}")


def check_debian_version(v: str) -> None:
    """A Debian source package's own version, with its revision: the
    fetched project is a Debian source (cryptography-insecure rebuilds each
    suite's python-cryptography), and a 3.0 (quilt) source needs a revision."""
    if ":" in v:
        fail(f"--upstream-debian-version {v!r} has an epoch; pass it as --epoch instead")
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~-]*-[A-Za-z0-9.+~]+", v):
        fail(f"--upstream-debian-version {v!r} is not a Debian version with a revision "
             "(<upstream>-<revision>, such as 43.0.0-3+deb13u1)")


def upstream_version(up: Path, match: str | None = None) -> str:
    """The fetched project's version, from its own tags at the pinned commit:
    any tag, or with `match` only those matching that glob (a mirror's
    `[mirror] tags`), so a packaging or experiment tag upstream
    (``debian/2.90-1``) is never taken for a version."""
    shallow = git(up, "rev-parse", "--is-shallow-repository") == "true"
    r = subprocess.run(["git", "-C", str(up), "describe", "--tags", "--long",
                        *(["--match", match] if match else [])],
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
    return upstream_from_describe(describe, strip_prefix=bool(match))


def declaration(tree: Path) -> tuple[Path, dict]:
    """The repository's declaration in `tree`, its packaging checkout
    (docs/packaging.md, "The declaration"); empty when it has none."""
    import tomllib
    path = tree / ".github/apt-packaging.toml"
    if not path.is_file():
        return path, {}
    try:
        return path, tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        fail(f"{path}: {e}")


def mirror_tag_match(tree: Path) -> str | None:
    """A mirror's `[mirror] tags` from its declaration in `tree`, the
    packaging checkout (docs/packaging.md, "Mirrors"): the glob upstream's
    version tags match, "[0-9]*" by default. None for any other kind."""
    path, decl = declaration(tree)
    if decl.get("kind") != "mirror":
        return None
    tags = (decl.get("mirror") or {}).get("tags", "[0-9]*")
    if not isinstance(tags, str) or not tags:
        fail(f"{path}: [mirror] tags must be a glob, such as \"[0-9]*\" or \"v[0-9]*\"")
    return tags


def package_version(base: str, upstream: str | None, owner_tag: str | None,
                    epoch: int | None, debian_source: bool = False) -> str:
    """Set B's version, or the patch series form around it; then the epoch.

    With debian_source, `upstream` is a Debian source package's own version,
    revision included: ours then extends Debian's revision
    (43.0.0-3+deb13u1+welland.0.0.post6), so it sorts above Debian's and
    above any earlier +<suffix> of ours on the same Debian version."""
    version = base
    if upstream is not None:
        if debian_source:
            check_debian_version(upstream)
        else:
            check_upstream(upstream, "--upstream-version")
        check_owner_tag(owner_tag)
        version = f"{upstream}+{owner_tag}.{base}"
    return with_epoch(version, epoch)


def check_owner_tag(owner_tag: str | None) -> None:
    if not owner_tag or not re.fullmatch(r"[a-z]+", owner_tag):
        fail(f"--owner-tag must be lower-case letters (fpgasonline, welland), not {owner_tag!r}")


def with_epoch(version: str, epoch: int | None) -> str:
    if epoch is None:
        return version
    if epoch < 1:
        fail(f"--epoch must be 1 or more, not {epoch}")
    return f"{epoch}:{version}"


def release_version(tag: str, hint: str = "") -> str:
    """An upstream release tag as a Debian upstream version, the way Set A
    normalises it (docs/packaging.md, "Set A"): ``v2.93`` is ``2.93``,
    ``netplan-1.1.2`` is ``1.1.2``, ``RELEASE_7_5`` is ``7.5``, ``3.8-rc3``
    is ``3.8~rc3`` and ``v2.94rc1`` is ``2.94~rc1``."""
    return tag_version(tag, f"upstream's release {tag!r}", hint, strip_prefix=True, release=True)


def set_a_base(release: str | None, n: int, sha: str, debian: str | None = None) -> str:
    """Set A's <base> (docs/packaging.md, "Set A"), from the upstream commit
    built: its nearest release (None: upstream has no tags), the upstream
    commits since, and its id. `debian` is Debian's version for that very
    release, when debian/ came from Debian."""
    if debian is not None:
        return debian
    if release is None:
        return f"0.0+git{n}.g{sha[:7]}-0"
    return f"{release}-0" if n == 0 else f"{release}+git{n}.g{sha[:7]}-0"


def upstream_commit(tree: Path, branch: str) -> tuple[str, str]:
    """The ref holding upstream's history, and the upstream commit HEAD is
    built on: their merge base. A CI checkout has only the remote's branches,
    so origin/<branch> comes first; a local clone may have only <branch>."""
    for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
        if subprocess.run(["git", "-C", str(tree), "rev-parse", "--verify", "--quiet",
                           f"{ref}^{{commit}}"], capture_output=True).returncode == 0:
            break
    else:
        fail(f"--upstream-branch {branch}: neither origin/{branch} nor {branch} exists. Set A "
             "keeps upstream's history on that branch (docs/packaging.md, \"Set A\"); check "
             "out with `fetch-depth: 0`, which fetches every branch.")
    r = subprocess.run(["git", "-C", str(tree), "merge-base", "HEAD", ref],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"HEAD has no history in common with {ref}: a Set A repository's default "
             "branch is upstream's history plus ours")
    return ref, r.stdout.strip()


def release_subject(tree: Path, given: str | None) -> re.Pattern | None:
    """The pattern upstream's release commits' subjects match, for an
    upstream whose release tags aren't in the built branch's history:
    --upstream-release-subject, else the declaration's `[version]
    release-subject`. None (the usual case): the releases are the tags.
    The declaration is checked either way, as PKG-DECLARED checks it."""
    path, decl = declaration(tree)
    table = decl.get("version", {})
    if not isinstance(table, dict):
        fail(f"{path}: [version] must be a table")
    extra = sorted(set(table) - {"release-subject"})
    if extra:
        fail(f"{path}: [version] has unknown keys {', '.join(extra)}")
    what = "--upstream-release-subject"
    if given is None:
        given, what = table.get("release-subject"), f"{path}: [version] release-subject"
        if given is None:
            return None
    try:
        if not isinstance(given, str) or not given:
            raise re.error("it must be a regular expression")
        pattern = re.compile(given)
    except re.error as e:
        fail(f"{what} {given!r}: {e}")
    if pattern.groups > 1:
        fail(f"{what} {given!r} has {pattern.groups} groups: at most one, around the release")
    return pattern


def last_release(tree: Path, commit: str, match: str | None,
                 subject: re.Pattern | None) -> tuple[str | None, int]:
    """The upstream release `commit` follows, as a version, and the upstream
    commits since it. (None, every commit) when upstream has no tags at all."""
    if subject is not None:
        # The nearest release commit on the branch's own line (its first
        # parents), not the newest by date in everything merged into it: a
        # maintenance release merged in later (7.4.1, after 7.5) is not what
        # the branch follows. The commits since it are all of them, merged
        # ones too, as git describe counts. Python's regular expressions,
        # not git's --grep.
        for line in git(tree, "log", "--first-parent", "--format=%H %s", commit).split("\n"):
            sha, _, text = line.partition(" ")
            m = subject.search(text)
            if not m:
                continue
            found = m.group(1) if subject.groups else m.group(0)
            if found is None:
                fail(f"the release pattern {subject.pattern!r} matches {sha[:7]}'s subject "
                     f"{text!r} without its group: the group must hold the release")
            release = release_version(found, f" It is what the release pattern "
                                      f"{subject.pattern!r} matched in {sha[:7]}'s subject.")
            return release, int(git(tree, "rev-list", "--count", f"{sha}..{commit}"))
        fail(f"no commit on upstream's own line (the first parents of {commit[:7]}) has a "
             f"subject matching the release pattern {subject.pattern!r}")
    r = subprocess.run(["git", "-C", str(tree), "describe", "--tags", "--long",
                        *(["--match", match] if match else []), commit],
                       capture_output=True, text=True)
    if r.returncode != 0:
        if match:
            fail(f"no tag matching --upstream-tag-match {match!r} in upstream's history "
                 f"({commit[:7]}). If upstream's release tags aren't on the branch it is "
                 "built from, name its release commits instead: [version] release-subject "
                 "(docs/packaging.md, \"Set A\").")
        n = int(git(tree, "rev-list", "--count", commit))
        # The document's base 4, but also what a checkout without tags, or an
        # upstream whose tags are off the built branch, looks like: say so.
        warn(f"no upstream release tag in the history of {commit[:7]}, so the version starts "
             f"0.0+git{n}. If upstream has releases, this sorts below them: check the "
             "checkout has upstream's tags (`fetch-depth: 0`), or, if its release tags "
             "aren't on the branch it is built from, declare its release commits' subject, "
             "[version] release-subject (docs/packaging.md, \"Set A\").")
        return None, n
    tag, n = parse_describe(r.stdout.strip())
    return (release_version(tag, " Pass --upstream-tag-match with a glob only the release "
                            "tags match."), n)


def committed_debian(src: Path, owner_tag: str, epoch: int | None) -> str | None:
    """The committed debian/changelog's top version, as Debian's: without its
    epoch, and without our own `+<owner-tag>...` when the top entry is one of
    ours on Debian's (``1.1.2-7+welland1`` is ``1.1.2-7``). None without a
    committed changelog. A version with an epoch needs the same --epoch,
    whatever the base turns out to be: without it every build sorts below
    Debian's."""
    committed = subprocess.run(["git", "-C", str(src), "show", "HEAD:./debian/changelog"],
                               capture_output=True, text=True, errors="replace")
    m = re.match(r"\S+ \(([^)\s]+)\)", committed.stdout) if committed.returncode == 0 else None
    if not m:
        return None
    theirs, _, version = m.group(1).rpartition(":")
    if theirs and theirs != str(epoch):
        fail(f"the committed debian/changelog's version {m.group(1)} has an epoch, so every "
             f"version without it sorts below that one: pass --epoch {theirs} (a declared "
             "PKG-VERSION exception)")
    return re.split(rf"\+{owner_tag}(?=[0-9.])", version, maxsplit=1)[0]


def debian_base(debian: str | None, release: str, n: int) -> str | None:
    """Debian's version as Set A's base: `debian` (committed_debian's) when it
    is ``<release>-<revision>`` for the very release the upstream commit is
    (n = 0), so ours extends Debian's revision and sorts above Debian's build
    of that release. Debian's repack of the release counts as the release
    when it is marked ``+dfsg...`` or ``+ds...`` (``2.93+dfsg-1``): those
    sort below the ``+git<N>`` of the commits after it.

    None when it is for another version, has no Debian revision, or the
    upstream commit is past the release. Any other suffix on the release
    (``2.0+repack-1``, ``2.0+really1.9-1``, ``2.0.ds1-1``) is refused: it
    sorts above ``2.0+git<N>``, so Debian's package would replace ours, or
    ours would go backwards at the next upstream commit."""
    if debian is None:
        return None
    upstream, dash, _ = debian.rpartition("-")
    if not dash or not upstream.startswith(release):
        return None
    rest = upstream[len(release):]
    if rest and not re.match(r"\+(?:dfsg|ds)", rest):
        if rest[0] == "+" or re.match(r"\.(?:dfsg|ds)", rest):
            fail(f"the committed debian/changelog's version {debian} is release {release} with "
                 f"{rest!r}, which sorts above {release}+git<N>: only Debian's +dfsg and +ds "
                 "repack suffixes are supported (docs/packaging.md, \"Set A\")")
        return None   # another version: 2.93 is not 2.9, 3.5a not 3.5, 2.0~rc1 not 2.0
    return debian if n == 0 else None


def set_a_version(tree: Path, src: Path, branch: str, match: str | None, subject: str | None,
                  owner_tag: str | None, epoch: int | None) -> str:
    """Set A: <base>+<owner-tag><M>, then the epoch."""
    check_owner_tag(owner_tag)
    check_not_shallow(tree)
    ref, commit = upstream_commit(tree, branch)
    release, n = last_release(tree, commit, match, release_subject(tree, subject))
    debian = committed_debian(src, owner_tag, epoch)
    debian = debian_base(debian, release, n) if release else None
    ours = git(tree, "rev-list", "--count", f"{ref}..HEAD")
    return with_epoch(f"{set_a_base(release, n, commit, debian)}+{owner_tag}{ours}", epoch)


def refuse_set_b_for_set_a(tree: Path) -> None:
    """A repository declared Set A never gets the Set B form: made from our
    own vX.Y tags, which a Set A repository doesn't have, it would be
    0.0.post<N>, below everything published. (A backport's form is still to
    come, so a backport is left as it was.)"""
    path, decl = declaration(tree)
    if decl.get("kind") == "A" and decl.get("variant") != "backport":
        fail(f"{path} says kind = \"A\", so the version is Set A's: pass --owner-tag "
             "<owner-tag> --upstream-branch upstream (the build's version-args; "
             "docs/packaging.md, \"Set A\")")


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


def write_changelog(src: Path, tree: Path, version: str, suite: str, repo: str,
                    fetched: str | None = None) -> None:
    """The entry goes in src's debian/changelog; the commit it names is tree's.

    With fetched (the Debian version the pin names), src is a Debian source
    fetched at build time, not a git checkout: its own debian/changelog
    (Debian's history) stays under ours, and must be that version's."""
    if not (src / "debian/control").is_file():
        fail(f"{src / 'debian/control'} does not exist, so there is no changelog to write")
    control = (src / "debian/control").read_text()
    if fetched:
        if not (src / "debian/changelog").is_file():
            fail(f"{src / 'debian/changelog'} does not exist: a fetched Debian source has one")
        old = (src / "debian/changelog").read_text()
    else:
        # The committed changelog, not the working tree's, so running this
        # twice doesn't stack two entries. Set B commits none: the entry is
        # the file. (HEAD:./ is relative to src, which may be below its
        # repository's root.)
        committed = subprocess.run(["git", "-C", str(src), "show", "HEAD:./debian/changelog"],
                                   capture_output=True, text=True)
        old = committed.stdout if committed.returncode == 0 else ""
    top = old.split("\n -- ", 1)[0]
    if BUILT_FROM in top and fetched:
        fail("the fetched debian/changelog already starts with a build's generated entry: "
             "fetch the source afresh for each build.")
    if BUILT_FROM in top:
        fail("the committed debian/changelog starts with a build's generated entry. "
             "Commit the changelog without it: the build adds its own.")
    if fetched:
        # The version is built from the pin, the code from the tree: if they
        # differ (a Debian update fetched under an old pin), the package
        # would carry one version's code under the other's number, and
        # Debian's own build of the newer version would replace ours.
        m = re.match(r"\S+ \(([^)]+)\)", old)
        have = m.group(1) if m else None
        if have != fetched:
            fail(f"the fetched debian/changelog is for {have or 'no version'}, not "
                 f"--upstream-debian-version {fetched}: the tree isn't the pinned source")
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
    up.add_argument("--upstream-debian-version", default=None, metavar="VERSION",
                    help="patch series of a Debian source package: its own version, revision "
                         "included (43.0.0-3+deb13u1); the tree's debian/changelog is kept")
    up.add_argument("--upstream-dir", type=Path, default=None,
                    help="patch series: the fetched project's checkout, at the pinned commit; "
                         "its git describe --tags gives the version")
    up.add_argument("--upstream-branch", default=None, metavar="BRANCH",
                    help="Set A: the branch holding upstream's unmodified history, usually "
                         "\"upstream\" (origin/BRANCH, else BRANCH). The version is "
                         "<base>+<owner-tag><M>")
    ap.add_argument("--upstream-tag-match", default=None, metavar="GLOB",
                    help="with --upstream-dir: only upstream tags matching GLOB are versions, and "
                         "a project-name prefix is dropped (netplan-1.1.2 is 1.1.2). Default: a "
                         "mirror's [mirror] tags (\"[0-9]*\" unless declared), from "
                         "--version-tree's .github/apt-packaging.toml; any tag otherwise. With "
                         "--upstream-branch: only tags matching GLOB are releases")
    ap.add_argument("--upstream-release-subject", default=None, metavar="REGEX",
                    help="with --upstream-branch, for an upstream whose release tags aren't in "
                         "the branch's history: a release is the nearest commit on the "
                         "branch's own line (first parents) whose subject matches REGEX, and "
                         "its one group (the whole match without one) is the release, "
                         "normalised as a tag is. Default: [version] release-subject in "
                         ".github/apt-packaging.toml, which is where a pattern with a space "
                         "must go (version-args is split at spaces); without either, the tags")
    ap.add_argument("--owner-tag", default=None,
                    help="patch series: the owner's tag between the two versions (fpgasonline); "
                         "Set A: the one before <M> (welland)")
    ap.add_argument("--epoch", type=int, default=None,
                    help="an epoch, only under a declared PKG-VERSION exception")
    ap.add_argument("--write-changelog", action="store_true",
                    help="write this build's entry to debian/changelog, above the committed one if any")
    args = ap.parse_args()
    if args.pr is not None and args.pr <= 0:
        fail(f"--pr must be a pull request number, not {args.pr}")
    debian_source = args.upstream_debian_version is not None
    set_a = args.upstream_branch is not None
    patch_series = (args.upstream_version is not None or args.upstream_dir is not None
                    or debian_source)
    if (patch_series or set_a) != (args.owner_tag is not None):
        fail("a patch series needs --owner-tag and one of --upstream-version, "
             "--upstream-debian-version or --upstream-dir; Set A needs --owner-tag and "
             "--upstream-branch")
    tree = args.version_tree or args.source_dir
    if args.upstream_tag_match is not None and args.upstream_dir is None and not set_a:
        fail("--upstream-tag-match needs --upstream-dir or --upstream-branch")
    if args.upstream_release_subject is not None and not set_a:
        fail("--upstream-release-subject needs --upstream-branch")
    if set_a:
        base = set_a_version(tree, args.source_dir, args.upstream_branch, args.upstream_tag_match,
                             args.upstream_release_subject, args.owner_tag, args.epoch)
    else:
        if not patch_series:
            refuse_set_b_for_set_a(tree)
        match = args.upstream_tag_match or mirror_tag_match(tree)
        upstream = (upstream_version(args.upstream_dir, match) if args.upstream_dir is not None
                    else args.upstream_debian_version if debian_source
                    else args.upstream_version)
        base = package_version(base_version(tree), upstream, args.owner_tag, args.epoch,
                               debian_source)
    version = with_suffixes(base, args.suite, args.pr)
    if args.write_changelog:
        # The fetched changelog carries Debian's epoch, which the flag can't
        # (check_debian_version refuses it; it goes in --epoch): compare with
        # both, so our epoch must also be Debian's.
        pinned = args.upstream_debian_version
        if pinned is not None and args.epoch is not None:
            pinned = f"{args.epoch}:{pinned}"
        write_changelog(args.source_dir, tree, version, args.suite,
                        args.repo or github_repository(tree), fetched=pinned)
    print(version)


if __name__ == "__main__":
    main()
