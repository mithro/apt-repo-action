#!/usr/bin/env python3
"""Keep earlier package versions from the live site in a deploy, up to a size limit.

A GitHub Pages deploy replaces the whole site, so without this every publish
deletes the previous version of every package. A client that fetched the
index a moment before the deploy then asks for files that no longer exist and
fails mid-install, and nobody can go back to an earlier version.

For each suite, the live site's <suite>/Packages lists what the previous
deploy served (including the versions it had itself kept). Every version
older than the one being published is a candidate; they are added a
generation at a time -- every package's previous version first, then the one
before that -- while the whole deploy stays under the limit, so space goes to
covering every package once before any package gets a deeper history. The
kept .debs sit next to the new ones and are indexed with them
(dpkg-scanpackages --multiversion), so they stay installable as
<package>=<version>.

Only versions LOWER than the new build's are kept: a publish that
deliberately goes back to an earlier version must not be overridden by a
higher kept one. Packages the new build no longer produces are dropped.

Keeping history is best effort: an unreachable site or a file that does not
match its index is reported and skipped, never a failed publish.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.request


def fetch(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "apt-repo-action keep-history"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"::warning::{url}: HTTP {e.code}; keeping no history from it")
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"::warning::{url}: {e}; keeping no history from it")
        return None


def stanzas(text: str) -> list[dict[str, str]]:
    out = []
    for block in text.split("\n\n"):
        fields = dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", block, re.M))
        if "Package" in fields and "Filename" in fields:
            out.append(fields)
    return out


def compare(a: str, b: str) -> int:
    """dpkg's own version ordering."""
    if a == b:
        return 0
    lt = subprocess.run(["dpkg", "--compare-versions", a, "lt", b]).returncode == 0
    return -1 if lt else 1


def local_versions(suite_dir: pathlib.Path) -> dict[tuple[str, str], list[str]]:
    """{(package, arch): [version, ...]} of the .debs the new build put in suite_dir."""
    found: dict[tuple[str, str], list[str]] = {}
    for deb in sorted(suite_dir.glob("*.deb")):
        out = subprocess.run(["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture"],
                             check=True, capture_output=True, text=True).stdout
        f = dict(re.findall(r"^([A-Za-z]+): (.*)$", out, re.M))
        found.setdefault((f["Package"], f["Architecture"]), []).append(f["Version"])
    return found


def tree_size(root: pathlib.Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", required=True, help="live site URL")
    ap.add_argument("--dest", required=True, help="tree about to be indexed and deployed")
    ap.add_argument("--suites", required=True, help="space-separated suites")
    ap.add_argument("--limit-bytes", type=int, required=True,
                    help="keep the whole deploy under this many bytes")
    ap.add_argument("--reserve-bytes", type=int, default=0,
                    help="bytes other steps will still add (index files, legacy copies)")
    args = ap.parse_args()

    site = args.site.rstrip("/")
    dest = pathlib.Path(args.dest)
    budget = args.limit_bytes - args.reserve_bytes - tree_size(dest)
    print(f"history budget: {budget / 1e6:.1f} MB")
    if budget <= 0:
        print("::warning::the new build alone fills the size limit; keeping no earlier versions")
        return

    # One chain per (suite, package, arch), newest first.
    chains: list[list[tuple[str, dict[str, str]]]] = []
    for suite in args.suites.split():
        new = local_versions(dest / suite)
        index = fetch(f"{site}/{suite}/Packages")
        if index is None:
            continue
        old: dict[tuple[str, str], list[dict[str, str]]] = {}
        for st in stanzas(index.decode()):
            key = (st["Package"], st.get("Architecture", ""))
            if key not in new:
                continue  # the new build no longer produces this package
            newest = max(new[key], key=functools.cmp_to_key(compare))
            if compare(st["Version"], newest) < 0:
                old.setdefault(key, []).append(st)
        for key, sts in sorted(old.items()):
            sts.sort(key=functools.cmp_to_key(lambda a, b: compare(b["Version"], a["Version"])))
            chains.append([(suite, st) for st in sts])

    kept = skipped = 0
    used = 0
    cut: set[int] = set()  # chains that lost a generation keep no older ones
    depth = max((len(c) for c in chains), default=0)
    for gen in range(depth):
        for i, chain in enumerate(chains):
            if gen >= len(chain) or i in cut:
                continue
            suite, st = chain[gen]
            size = int(st.get("Size", "0"))
            if used + size > budget:
                cut.add(i)
                skipped += 1
                continue
            name = os.path.basename(st["Filename"])
            out = dest / suite / name
            if out.exists():
                continue  # the new build already has this exact file
            data = fetch(f"{site}/{suite}/{st['Filename'].removeprefix('./')}")
            if data is None or len(data) != size or \
                    hashlib.sha256(data).hexdigest() != st.get("SHA256"):
                print(f"::warning::{suite}/{name}: missing or does not match the live index; not kept")
                cut.add(i)
                continue
            out.write_bytes(data)
            used += size
            kept += 1
            print(f"kept {suite}/{name}")
    print(f"kept {kept} earlier versions ({used / 1e6:.1f} MB); "
          f"{skipped} did not fit in {args.limit_bytes / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
