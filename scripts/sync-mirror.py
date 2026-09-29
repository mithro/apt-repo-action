#!/usr/bin/env python3
"""Make a mirror repository's copies of upstream's branches and tags exact.

For a repository whose .github/apt-packaging.toml says `kind = "mirror"`
(docs/packaging.md, "Mirrors"). Every branch and tag of the declared
`upstream` is pushed here under the same name, forced, so a copy is always
identical to upstream's, even when upstream rewrites it. What is ours is never
touched:
- the default branch (`packaging`) and the branches in `[mirror] ours`;
- a tag here that is on the default branch's history (its `v0.0`), even if
  upstream has one of the same name.
Nothing here is ever deleted: a branch upstream deletes stays, with a
warning (migen's choice; docs/packaging.md marks it as pending Tim's
confirmation).

The branches are pushed together, atomically; then the tags, not
atomically, so a tag the repository's tag ruleset refuses is skipped with a
warning and never holds up the copies. Warnings go to the run's summary too:
our tag not overwritten, a refused tag, a branch gone upstream, and an
upstream rewrite of the built branch (its new tip not a descendant of the
old), after which the package's version can go down.

Prints what changed. With $GITHUB_OUTPUT set, it also writes
``build=true|false``: whether `[mirror] build`, the branch the package is
built from, moved, so the caller knows to start the build (a push made with
the workflow's own token starts no workflow by itself).

    sync-mirror.py --declaration .github/apt-packaging.toml [--remote origin] [--default packaging] [--dry-run]

Run from a clone of this repository whose `--remote` can be pushed to. Moved
here from fpgas-online/migen's packaging/sync-mirror.py, which it replaces.

Standard library only.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path


class Error(Exception):
    pass


def run(*args: str, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(args), flush=True)
    r = subprocess.run(args, capture_output=True, text=True)
    if r.stdout.strip() and not quiet:
        print(r.stdout.rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr)
    if check and r.returncode != 0:
        raise Error(f"{' '.join(args)} failed ({r.returncode})")
    return r


def warn(msg: str) -> None:
    """A warning in the log and, in a workflow, in the run's summary, where
    it is seen without opening the log."""
    print(f"::warning::{msg}", flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"- ⚠️ {msg}\n")


def unshallow(remote: str) -> None:
    """actions/checkout clones one commit deep; ancestry needs the history."""
    if run("git", "rev-parse", "--is-shallow-repository", quiet=True).stdout.strip() == "true":
        run("git", "fetch", "--no-tags", "--unshallow", remote, quiet=True)


def is_ancestor(a: str, b: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", f"{a}^{{commit}}", f"{b}^{{commit}}"],
                          capture_output=True, text=True).returncode == 0


def push_tags(remote: str, specs: list[str]) -> list[str]:
    """Pushes the tags, not atomically, so one a tag ruleset refuses leaves
    the others; returns the refs refused (git push --porcelain: `!`)."""
    r = run("git", "push", "--porcelain", remote, *specs, check=False, quiet=True)
    refused = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] == "!":
            refused.append(parts[1].partition(":")[2])
    if r.returncode != 0 and not refused:
        raise Error(f"git push of the tags failed ({r.returncode}): {r.stderr.strip()}")
    return refused


def refs(remote: str) -> dict[str, str]:
    """ref name -> object id, for branches and tags (peeled tag lines skipped)."""
    out = run("git", "ls-remote", "--heads", "--tags", remote, quiet=True).stdout
    result = {}
    for line in out.splitlines():
        sha, name = line.split("\t")
        if not name.endswith("^{}"):
            result[name] = sha
    return result


def load(path: Path) -> tuple[str, str, set[str]]:
    """The declaration's `upstream`, `[mirror] build` and `[mirror] ours`."""
    try:
        decl = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Error(f"{path}: {e}")
    if decl.get("kind") != "mirror":
        raise Error(f'{path}: kind is {decl.get("kind")!r}, not "mirror"')
    upstream, m = decl.get("upstream"), decl.get("mirror")
    if not isinstance(upstream, str) or not upstream:
        raise Error(f"{path}: `upstream` must be the git URL this repository copies")
    if not isinstance(m, dict) or not isinstance(m.get("build"), str) or not m["build"]:
        raise Error(f"{path}: [mirror] build must name the branch the package is built from")
    ours = m.get("ours", [])
    if not isinstance(ours, list) or not all(isinstance(o, str) for o in ours):
        raise Error(f"{path}: [mirror] ours must be a list of branch names")
    return upstream, m["build"], set(ours)


def ours_by_history(remote: str, default: str, names: list[str]) -> set[str]:
    """Of the tags `names`, which exist here on `default`'s history: our own
    version tags, which upstream must not overwrite."""
    if not names:
        return set()
    unshallow(remote)
    run("git", "fetch", "--no-tags", remote, f"+refs/heads/{default}:refs/sync-mirror/default",
        *[f"+{n}:refs/sync-mirror/here/{n.removeprefix('refs/')}" for n in names], quiet=True)
    return {n for n in names if is_ancestor(f"refs/sync-mirror/here/{n.removeprefix('refs/')}",
                                            "refs/sync-mirror/default")}


def sync(upstream: str, build: str, ours: set[str], remote: str, default: str, dry_run: bool) -> bool:
    """Copy upstream here; returns whether the build branch moved."""
    ours = ours | {default}
    theirs, here = refs(upstream), refs(remote)
    if f"refs/heads/{build}" not in theirs:
        raise Error(f"{upstream} has no {build} branch")

    def our_branch(n: str) -> bool:
        return n.startswith("refs/heads/") and n.removeprefix("refs/heads/") in ours

    changed = [n for n, sha in sorted(theirs.items()) if here.get(n) != sha and not our_branch(n)]
    for n in sorted(n for n in theirs if our_branch(n)):
        warn(f"upstream has {n}, which is one of ours here: not mirrored")
    # A tag here that upstream has too, differently: ours if it is on our
    # default branch's history (packaging's v0.0), else a copy to update.
    clash = [n for n in changed if n.startswith("refs/tags/") and n in here]
    for n in sorted(ours_by_history(remote, default, clash)):
        warn(f"upstream has {n}, which is one of ours here (on {default}): not mirrored")
        changed.remove(n)
    for n in sorted(set(here) - set(theirs)):
        if n.startswith("refs/heads/") and not our_branch(n):
            warn(f"{n} is gone upstream; left here as it is")

    build_ref = f"refs/heads/{build}"
    refused = []
    if changed:
        # Exactly the objects upstream's refs point at, into a private
        # namespace, then each pushed to the same name here.
        run("git", "fetch", "--no-tags", upstream,
            *[f"+{n}:refs/sync-mirror/{n.removeprefix('refs/')}" for n in changed])
        if build_ref in changed and build_ref in here:
            # A rewrite upstream can make the version's upstream half go
            # down, and apt then won't upgrade (docs/packaging.md, "Mirrors").
            unshallow(remote)
            run("git", "fetch", "--no-tags", remote, f"+{build_ref}:refs/sync-mirror/old/{build}", quiet=True)
            if not is_ancestor(f"refs/sync-mirror/old/{build}", f"refs/sync-mirror/heads/{build}"):
                warn(f"upstream rewrote {build}: {here[build_ref][:12]} is not an ancestor of the new "
                     f"{theirs[build_ref][:12]}, so the package's upstream version may go down, and apt "
                     "would not upgrade to it (docs/packaging.md, \"Mirrors\")")
        spec = {n: f"+refs/sync-mirror/{n.removeprefix('refs/')}:{n}" for n in changed}
        branches = [spec[n] for n in changed if not n.startswith("refs/tags/")]
        tags = [spec[n] for n in changed if n.startswith("refs/tags/")]
        if dry_run:
            print("dry run, would push:", *branches, *tags, sep="\n  ")
        else:
            # The branches together; then the tags, each on its own, so one a
            # tag ruleset refuses never holds up the copies.
            if branches:
                run("git", "push", "--atomic", remote, *branches)
            refused = push_tags(remote, tags) if tags else []
            for n in refused:
                warn(f"{remote} refused {n} (a tag ruleset?): not mirrored. A mirror's tag ruleset must admit "
                     "upstream's tag names (docs/packaging.md, \"Mirrors\")")
    changed = [n for n in changed if n not in refused]
    for n in changed:
        print(f"mirrored {n}: {here.get(n, '(new)')[:12]} -> {theirs[n][:12]}")
    if not changed:
        print("every mirrored branch and tag already matches upstream")
    return f"refs/heads/{build}" in changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--declaration", type=Path, default=Path(".github/apt-packaging.toml"))
    ap.add_argument("--remote", default="origin", help="this repository's remote")
    ap.add_argument("--default", default="packaging", help="this repository's default branch: ours")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        upstream, build, ours = load(args.declaration)
        moved = sync(upstream, build, ours, args.remote, args.default, args.dry_run)
    except Error as e:
        print(f"::error::sync-mirror.py: {e}")
        return 1
    print(f"build={'true' if moved else 'false'}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"build={'true' if moved else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
