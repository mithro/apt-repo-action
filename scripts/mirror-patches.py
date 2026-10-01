#!/usr/bin/env python3
"""Generate a mirror repository's debian/patches from its patch branches.

A mirror (docs/packaging.md, "Our own patches on a mirror") keeps each of
our changes to upstream as a branch, `patches/<topic>`, and pins the commit
built in its declaration:

    [[mirror.patches]]
    branch = "patches/streaming-axfr"
    commit = "4f1c2d9e..."

For each pin, in order, this writes `git format-patch <base>..<commit>` (one
patch per commit; `<base>` is where the branch leaves the built branch) into
`<out>/<topic>/`, and the quilt `series`, so a `3.0 (quilt)` build applies
them. Run in deb.yml after debian/ is copied into the checked-out build
branch, before build-deb:

    mirror-patches.py --declaration .github/apt-packaging.toml --source src

Every generated series is applied, in order, to a checkout of the built commit
with dpkg-source's own `patch` options before it is written out, and a
patch that doesn't apply fails it: dpkg-source --before-build would
otherwise skip the whole series and build unpatched, with exit 0. A patch
branch that changes a binary file, or creates or deletes an empty one, is
refused (quilt can't carry either). A
patch branch built on another (a stack) starts from that one's pin. With
--check, the same, into a scratch directory, saying which doesn't apply:
what the sync runs before it starts a build.

Standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


class Error(Exception):
    pass


class Conflict(Error):
    """A generated patch that doesn't apply, in order, to the built tree."""
    def __init__(self, topic: str, patch: str, why: str):
        super().__init__(f"{patch} doesn't apply to the built branch: {why}")
        self.topic, self.patch = topic, patch


# How dpkg-source applies each patch of a 3.0 (quilt) series
# (Dpkg::Source::Patch::apply, dpkg 1.22): the same tool and options here,
# so the check and the build can't disagree.
PATCH = ["patch", "-t", "-F", "0", "-N", "-p1", "-u", "-V", "never", "-b", "-z", ".dpkg-orig"]
# In debian/patches beside the series: tells build-deb they were generated,
# so it checks every one was applied (dpkg-source --before-build returns 0
# without applying anything when the first patch doesn't apply).
MARKER = ".generated"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise Error(f"git {' '.join(args)}: {r.stderr.strip() or r.returncode}")
    return r


def load(path: Path) -> list[dict]:
    """The declaration's [[mirror.patches]]: [{"branch", "commit", "topic"}]."""
    try:
        decl = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Error(f"{path}: {e}")
    raw = (decl.get("mirror") or {}).get("patches", [])
    if not isinstance(raw, list) or not all(isinstance(p, dict) for p in raw):
        raise Error(f"{path}: [[mirror.patches]] must be an array of tables")
    out, seen = [], set()
    for p in raw:
        branch, commit = p.get("branch"), p.get("commit")
        if not isinstance(branch, str) or not re.fullmatch(r"patches/[A-Za-z0-9._-]+", branch or ""):
            raise Error(f"{path}: a patch branch is patches/<topic>, not {branch!r}")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
            raise Error(f"{path}: {branch}'s commit must be a full 40-hex commit id, not {commit!r}")
        extra = sorted(set(p) - {"branch", "commit"})
        if extra:
            raise Error(f"{path}: {branch} has unknown keys {', '.join(extra)}")
        if branch in seen:
            raise Error(f"{path}: {branch} is listed twice")
        seen.add(branch)
        out.append({"branch": branch, "commit": commit, "topic": branch.removeprefix("patches/")})
    return out


def is_ancestor(src: Path, a: str, b: str) -> bool:
    return git(src, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def base_of(src: Path, patches: list[dict], i: int, head: str) -> str:
    """Where patches[i] starts: the nearest earlier pin it is built on (a
    stack, patches/b on patches/a), else where it leaves the built branch."""
    p = patches[i]
    for q in reversed(patches[:i]):
        if is_ancestor(src, q["commit"], p["commit"]):
            return q["commit"]
    base = git(src, "merge-base", head, p["commit"], check=False).stdout.strip()
    if not base:
        raise Error(f"{p['branch']} ({p['commit'][:12]}) shares no history with the built branch")
    return base


def apply_series(src: Path, head: str, out: Path, series: list[str]) -> None:
    """Applies the series, in order, to a copy of `head`'s tree with
    dpkg-source's patch options; raises Conflict at the first that fails."""
    # A checkout, as the build's is, not `git archive`: that honours
    # .gitattributes export-subst and export-ignore, and the build doesn't.
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "tree"
        git(src, "worktree", "add", "--quiet", "--detach", str(wt), head)
        try:
            for f in series:
                with open(out / f) as fh:
                    r = subprocess.run(PATCH, cwd=wt, stdin=fh, capture_output=True, text=True,
                                       env={**os.environ, "LC_ALL": "C", "PATCH_GET": "0"})
                if r.returncode:
                    why = (r.stdout + r.stderr).strip().splitlines()
                    raise Conflict(f.split("/", 1)[0], f, why[-1] if why else f"patch exit {r.returncode}")
        finally:
            git(src, "worktree", "remove", "--force", str(wt), check=False)


def generate(src: Path, patches: list[dict], out: Path, head: str = "HEAD") -> list[str]:
    """Writes each pin's patches under `out`/<topic>/ and `out`/series,
    once they all apply, in order, to `head` as dpkg-source will apply them;
    returns the series (paths relative to `out`). They are written into a
    directory beside `out` and moved into place only then, so a failure
    leaves nothing at `out`: no half-written series for a build to take."""
    # git -C <src> reads a relative -o from <src>: make both absolute.
    src, out = src.resolve(), out.resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Error(f"{out} already exists")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        series = stage_patches(src, patches, stage, head)
        apply_series(src, head, stage, series)
    except BaseException:
        shutil.rmtree(stage)  # our own scratch directory, just made
        raise
    if out.exists():
        out.rmdir()  # empty, checked above
    stage.rename(out)
    return series


def stage_patches(src: Path, patches: list[dict], out: Path, head: str) -> list[str]:
    """generate's first half: the patches and the series, into `out`."""
    series = []
    for i, p in enumerate(patches):
        if git(src, "cat-file", "-e", f"{p['commit']}^{{commit}}", check=False).returncode:
            raise Error(f"{p['branch']}: commit {p['commit'][:12]} isn't in {src}. Check the build branch "
                        "out with its history (fetch-depth: 0), or fetch the pinned commit.")
        base = base_of(src, patches, i, head)
        if base == p["commit"] or is_ancestor(src, p["commit"], head):
            raise Error(f"{p['branch']} ({p['commit'][:12]}) has nothing the built branch doesn't: "
                        "upstream took it? Remove it from the declaration.")
        d = out / p["topic"]
        d.mkdir(parents=True)
        files = git(src, "format-patch", "--zero-commit", "--no-signature", "--keep-subject",
                    "-o", str(d), f"{base}..{p['commit']}").stdout.split()
        for f in files:
            text = Path(f).read_text(errors="replace")
            if "\nGIT binary patch\n" in text or re.search(r"^Binary files .* differ$", text, re.M):
                raise Error(f"{p['branch']}: {Path(f).name} changes a binary file, which a quilt patch "
                            "can't carry (patch applies none of it, silently). Keep binary changes out of "
                            "patch branches.")
            # e69de29 is git's empty blob: such a file has no hunk, and patch
            # creates (or removes) nothing.
            if re.search(r"^index (0+\.\.e69de29[0-9a-f]*|e69de29[0-9a-f]*\.\.0+)$", text, re.M):
                raise Error(f"{p['branch']}: {Path(f).name} creates or deletes an empty file, which a quilt "
                            "patch can't carry (patch does nothing, silently). Give the file content, or "
                            "create it in debian/rules.")
        series += [str(Path(f).relative_to(out)) for f in files]
    (out / "series").write_text("".join(f"{x}\n" for x in series))
    return series


def check(src: Path, patches: list[dict], head: str = "HEAD") -> tuple[str, str] | None:
    """Whether the patches apply to `head`, exactly as the build checks
    (generate, into a scratch directory): the first (topic, patch) that
    doesn't, or None."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            generate(src, patches, Path(tmp) / "patches", head)
        except Conflict as c:
            return c.topic, c.patch
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--declaration", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True,
                    help="the build branch's checkout, with debian/ copied in")
    ap.add_argument("--out", type=Path, default=None, help="default: <source>/debian/patches")
    ap.add_argument("--head", default="HEAD", help="the built commit (default HEAD)")
    ap.add_argument("--check", action="store_true",
                    help="only check that the patches apply to --head, as the build applies them "
                         "(dpkg-source's patch options, on a checkout of --head)")
    args = ap.parse_args()
    try:
        patches = load(args.declaration)
        if not patches:
            print("no [[mirror.patches]]: nothing to generate")
            return 0
        if args.check:
            bad = check(args.source, patches, args.head)
            if bad:
                print(f"::error::patches/{bad[0]}: {bad[1]} doesn't apply to {args.head}")
                return 1
            print(f"every patch applies to {args.head}")
            return 0
        fmt = args.source / "debian/source/format"
        if not fmt.is_file() or fmt.read_text().strip() != "3.0 (quilt)":
            raise Error(f"{fmt} must say `3.0 (quilt)`, or dpkg-buildpackage won't apply the patches")
        out = args.out or args.source / "debian/patches"
        if out.exists() and any(out.iterdir()):
            raise Error(f"{out} already has files: packaging must not commit debian/patches; "
                        "they are generated from the patch branches")
        series = generate(args.source, patches, out, args.head)
        (out / MARKER).write_text("Generated by mithro/apt-repo-action's scripts/mirror-patches.py from "
                                  "the declaration's [[mirror.patches]]. build-deb checks every patch in "
                                  "series was applied.\n")
        print(f"{len(series)} patches from {len(patches)} branches, each applies:", *series, sep="\n  ")
    except Error as e:
        print(f"::error::mirror-patches.py: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
