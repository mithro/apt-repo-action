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

With --check it generates them into a scratch directory and applies them
with `git am` to --source's HEAD in a scratch worktree instead, and says
which one doesn't apply: what the sync runs before it starts a build.

Standard library only.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


class Error(Exception):
    pass


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


def generate(src: Path, patches: list[dict], out: Path, head: str = "HEAD") -> list[str]:
    """Writes each pin's patches under `out`/<topic>/ and `out`/series;
    returns the series (paths relative to `out`)."""
    series = []
    for p in patches:
        if git(src, "cat-file", "-e", f"{p['commit']}^{{commit}}", check=False).returncode:
            raise Error(f"{p['branch']}: commit {p['commit'][:12]} isn't in {src}. Check the build branch "
                        "out with its history (fetch-depth: 0), or fetch the pinned commit.")
        base = git(src, "merge-base", head, p["commit"], check=False).stdout.strip()
        if not base:
            raise Error(f"{p['branch']} ({p['commit'][:12]}) shares no history with the built branch")
        if base == p["commit"]:
            raise Error(f"{p['branch']} ({p['commit'][:12]}) has nothing the built branch doesn't: "
                        "upstream took it? Remove it from the declaration.")
        d = out / p["topic"]
        d.mkdir(parents=True)
        files = git(src, "format-patch", "--zero-commit", "--no-signature", "--keep-subject",
                    "-o", str(d), f"{base}..{p['commit']}").stdout.split()
        series += [str(Path(f).relative_to(out)) for f in files]
    (out / "series").write_text("".join(f"{x}\n" for x in series))
    return series


def check(src: Path, patches: list[dict], head: str = "HEAD") -> tuple[str, str] | None:
    """Applies the generated patches, in order, to `head` in a scratch
    worktree; the first (topic, patch) that doesn't apply, or None."""
    with tempfile.TemporaryDirectory() as tmp:
        out, wt = Path(tmp) / "patches", Path(tmp) / "wt"
        series = generate(src, patches, out, head)
        git(src, "worktree", "add", "-q", "--detach", str(wt), head)
        try:
            for f in series:
                r = git(wt, "-c", "user.name=mirror-patches", "-c", "user.email=mirror-patches@invalid",
                        "am", "-q", str(out / f), check=False)
                if r.returncode:
                    git(wt, "am", "--abort", check=False)
                    return f.split("/", 1)[0], f
        finally:
            git(src, "worktree", "remove", "--force", str(wt), check=False)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--declaration", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True,
                    help="the build branch's checkout, with debian/ copied in")
    ap.add_argument("--out", type=Path, default=None, help="default: <source>/debian/patches")
    ap.add_argument("--head", default="HEAD", help="the built commit (default HEAD)")
    ap.add_argument("--check", action="store_true",
                    help="only check that the patches apply to --head, with git am")
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
        print(f"{len(series)} patches from {len(patches)} branches:", *series, sep="\n  ")
    except Error as e:
        print(f"::error::mirror-patches.py: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
