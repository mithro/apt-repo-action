#!/usr/bin/env python3
"""Make a mirror repository's copies of upstream's branches and tags exact.

For a repository whose .github/apt-packaging.toml says `kind = "mirror"`
(docs/packaging.md, "Mirrors"). Every branch and tag of the declared
`upstream` is pushed here under the same name, forced, so a copy is always
identical to upstream's, even when upstream rewrites it. What is ours is never
touched:
- the default branch (and whatever branch this repository's HEAD names,
  whatever --default says), the branches in `[mirror] ours`, and every
  `patches/*` branch;
- `archive/*` tags, and a tag on the default branch's history (its `v0.0`),
  even if upstream has one of the same name.
Nothing here is ever deleted: a branch or tag upstream deletes stays, with a
warning for a branch. History is never lost, and a package built from it
stays reproducible.

With [[mirror.patches]], when the built branch moved it also checks our
patches still apply to the new tip (scripts/mirror-patches.py --check); if
one doesn't, `build` is false and `patch-conflict` says which.

The branches are pushed together, atomically, and `build` is written
then; the tags after, not atomically, and any the first push didn't take
one by one, so a tag the repository's tag ruleset refuses is skipped with a
warning and never holds up the copies or loses a build. Warnings go to the run's summary too:
our tag not overwritten, a refused tag, a branch gone upstream, and an
upstream rewrite of the built branch (its new tip not a descendant of the
old), after which the package's version can go down.

Prints what changed. With $GITHUB_OUTPUT set, it also writes
``build=true|false`` (the built branch moved and our patches apply: the
caller starts the build, since a push made with the workflow's own token
starts no workflow by itself) and ``patch-conflict``.

    sync-mirror.py --declaration .github/apt-packaging.toml --default <default branch> [--remote origin] [--dry-run]

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


OK_FLAGS = {" ", "+", "*", "="}  # git push --porcelain: fast-forward, forced, new, up to date


def push_status(r: subprocess.CompletedProcess) -> dict[str, str]:
    """git push --porcelain's lines: destination ref -> flag."""
    out = {}
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and len(parts[0]) == 1:
            out[parts[1].partition(":")[2]] = parts[0]
    return out


def push_tags(remote: str, specs: list[str]) -> list[str]:
    """Pushes the tags, not atomically; any a first push didn't take (a tag
    ruleset refusing one can refuse the whole push, GH013) are pushed one by
    one, so a refused tag never holds up the others. Returns those refused."""
    r = run("git", "push", "--porcelain", remote, *specs, check=False, quiet=True)
    done = {ref for ref, flag in push_status(r).items() if flag in OK_FLAGS}
    refused = []
    for spec in specs:
        ref = spec.partition(":")[2]
        if ref in done:
            continue
        one = run("git", "push", "--porcelain", remote, spec, check=False, quiet=True)
        if push_status(one).get(ref) not in OK_FLAGS:
            refused.append(ref)
    return refused


def output(key: str, value: str) -> None:
    """A step output, written at once: a later failure doesn't lose it."""
    print(f"{key}={value}", flush=True)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"{key}={value}\n")


def head_branch(remote: str) -> str | None:
    """The remote's default branch, from its HEAD."""
    r = run("git", "ls-remote", "--symref", remote, "HEAD", check=False, quiet=True)
    for line in r.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    return None


def mirror_patches():
    """scripts/mirror-patches.py, beside this script."""
    import importlib.machinery
    import importlib.util
    path = Path(__file__).resolve().with_name("mirror-patches.py")
    loader = importlib.machinery.SourceFileLoader("mirror_patches", str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("mirror_patches", loader))
    loader.exec_module(mod)
    return mod


def refs(remote: str) -> dict[str, str]:
    """ref name -> object id, for branches and tags (peeled tag lines skipped)."""
    out = run("git", "ls-remote", "--heads", "--tags", remote, quiet=True).stdout
    result = {}
    for line in out.splitlines():
        sha, name = line.split("\t")
        if not name.endswith("^{}"):
            result[name] = sha
    return result


def load(path: Path) -> tuple[str, str, set[str], list[dict]]:
    """The declaration's `upstream`, `[mirror] build`, `[mirror] ours` and
    `[[mirror.patches]]`."""
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
    try:
        patches = mirror_patches().load(path)
    except Exception as e:  # mirror_patches.Error, from another module
        raise Error(str(e))
    return upstream, m["build"], set(ours), patches


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


def ours_branch(name: str, ours: set[str]) -> bool:
    """A branch that is ours (docs/packaging.md, "Mirrors"): the default and
    [mirror] ours, and every patch branch."""
    b = name.removeprefix("refs/heads/")
    return name.startswith("refs/heads/") and (b in ours or b.startswith("patches/"))


def sync(upstream: str, build: str, ours: set[str], remote: str, default: str, dry_run: bool,
         patches: list[dict] = ()) -> bool:
    """Copies upstream here, and says whether to build: the built branch
    moved and, with patches, they still apply to it. Writes the step outputs
    `build` (as soon as the branches are pushed) and `patch-conflict`."""
    if not default:
        raise Error("the default branch is empty (a scheduled run's event has no repository): "
                    "pass --default, the repository's default branch")
    # Whatever --default says, the branch the remote's HEAD names is ours.
    ours = ours | {default} | ({h} if (h := head_branch(remote)) else set())
    theirs, here = refs(upstream), refs(remote)
    build_ref = f"refs/heads/{build}"
    if build_ref not in theirs:
        raise Error(f"{upstream} has no {build} branch")

    changed = [n for n, sha in sorted(theirs.items()) if here.get(n) != sha and not ours_branch(n, ours)]
    for n in sorted(n for n in theirs if ours_branch(n, ours)):
        warn(f"upstream has {n}, which is one of ours here: not mirrored")
    # A tag here that upstream has too, differently: ours if it is an
    # archive/ tag or on our default branch's history (packaging's v0.0),
    # else a copy to update.
    for n in sorted(n for n in changed if n.startswith("refs/tags/archive/") and n in here):
        warn(f"upstream has {n}, which is one of ours here: not mirrored")
        changed.remove(n)
    clash = [n for n in changed if n.startswith("refs/tags/") and n in here]
    for n in sorted(ours_by_history(remote, default, clash)):
        warn(f"upstream has {n}, which is one of ours here (on {default}): not mirrored")
        changed.remove(n)
    for n in sorted(set(here) - set(theirs)):
        if n.startswith("refs/heads/") and not ours_branch(n, ours):
            warn(f"{n} is gone upstream; left here as it is (nothing is deleted)")

    moved = build_ref in changed
    refused, conflict = [], None
    if changed:
        # Exactly the objects upstream's refs point at, into a private
        # namespace, then each pushed to the same name here.
        run("git", "fetch", "--no-tags", upstream,
            *[f"+{n}:refs/sync-mirror/{n.removeprefix('refs/')}" for n in changed])
        new_tip = f"refs/sync-mirror/heads/{build}"
        if moved and build_ref in here:
            # A rewrite upstream can make the version's upstream half go
            # down, and apt then won't upgrade (docs/packaging.md, "Mirrors").
            unshallow(remote)
            run("git", "fetch", "--no-tags", remote, f"+{build_ref}:refs/sync-mirror/old/{build}", quiet=True)
            if not is_ancestor(f"refs/sync-mirror/old/{build}", new_tip):
                warn(f"upstream rewrote {build}: {here[build_ref][:12]} is not an ancestor of the new "
                     f"{theirs[build_ref][:12]}, so the package's upstream version may go down, and apt "
                     "would not upgrade to it (docs/packaging.md, \"Mirrors\")")
        if moved and patches:
            # Our patches, as the build would generate them, on the new tip.
            unshallow(remote)
            run("git", "fetch", "--no-tags", remote, "+refs/heads/patches/*:refs/sync-mirror/patches/*",
                "+refs/tags/archive/*:refs/sync-mirror/archive/*", quiet=True)
            mp = mirror_patches()
            try:
                bad = mp.check(Path.cwd(), patches, new_tip)
            except mp.Error as e:
                raise Error(f"checking our patches on {build} {theirs[build_ref][:12]}: {e}")
            if bad:
                conflict = f"patches/{bad[0]}: {bad[1]} doesn't apply to {build} {theirs[build_ref][:12]}"
                warn(f"{conflict}. Not building: the last good package stays published. Rebase the patch "
                     "branch and move its pin (docs/packaging.md, \"Our own patches on a mirror\")")
        spec = {n: f"+refs/sync-mirror/{n.removeprefix('refs/')}:{n}" for n in changed}
        branches = [spec[n] for n in changed if not n.startswith("refs/tags/")]
        tags = [spec[n] for n in changed if n.startswith("refs/tags/")]
        if dry_run:
            print("dry run, would push:", *branches, *tags, sep="\n  ")
        elif branches:
            run("git", "push", "--atomic", remote, *branches)
    build_now = moved and conflict is None
    output("build", "true" if build_now else "false")
    output("patch-conflict", conflict or "")
    if changed and not dry_run and tags:
        # The tags after `build` is out, so a tag that fails loses no build.
        refused = push_tags(remote, tags)
        for n in refused:
            warn(f"{remote} refused {n} (a tag ruleset?): not mirrored. A mirror's tag ruleset must admit "
                 "upstream's tag names (docs/packaging.md, \"Mirrors\")")
    for n in changed:
        if n not in refused:
            print(f"mirrored {n}: {here.get(n, '(new)')[:12]} -> {theirs[n][:12]}")
    if not changed:
        print("every mirrored branch and tag already matches upstream")
    return build_now


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--declaration", type=Path, default=Path(".github/apt-packaging.toml"))
    ap.add_argument("--remote", default="origin", help="this repository's remote")
    ap.add_argument("--default", required=True, help="this repository's default branch: ours")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        upstream, build, ours, patches = load(args.declaration)
        sync(upstream, build, ours, args.remote, args.default, args.dry_run, patches)
    except Error as e:
        print(f"::error::sync-mirror.py: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
